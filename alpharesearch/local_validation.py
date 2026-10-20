"""Offline, causal daily-panel validation. This is not BRAIN's simulator.

The data owner supplies PIT membership and prices. No local evaluator or holdout
tool is exposed to the Research Agent. A separate ledger permits one final reveal.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import sqlite3
import statistics as stats
from datetime import date
from pathlib import Path

from .common import ResearchError, digest, dumps, now
from .validator import Validator
from . import holdout_registry as registry


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def code_hash():
    return digest({p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))})


def number(value):
    try: result=float(value)
    except (ValueError,TypeError):return None
    return result if math.isfinite(result) else None


def rank(values):
    good=sorted((v,i) for i,v in enumerate(values) if v is not None)
    result=[None]*len(values);i=0
    while i<len(good):
        j=i+1
        while j<len(good) and good[j][0]==good[i][0]:j+=1
        average=(i+j-1)/2
        for _,index in good[i:j]:result[index]=average/max(1,len(good)-1)
        i=j
    return result


def corr(x,y):
    pairs=[(a,b) for a,b in zip(x,y) if a is not None and b is not None]
    if len(pairs)<3:return None
    a,b=zip(*pairs)
    if stats.pvariance(a)<1e-24 or stats.pvariance(b)<1e-24:return None
    return stats.correlation(a,b)


class Panel:
    def __init__(self,path,before=None,through=None):
        path=Path(path)
        if path.stat().st_size>100_000_000:raise ResearchError('本地 CSV 限制 100 MB')
        rows={};dates=set();assets=set();sessions={}
        with path.open(newline='',encoding='utf-8') as source:
            reader=csv.DictReader(source)
            required={'date','session','asset','available_date','eligible','close'}
            if not required<=set(reader.fieldnames or []):raise ResearchError('面板需 date,session,asset,available_date,eligible,close 列')
            self.columns=set(reader.fieldnames)-{'date','session','asset','available_date','eligible'}
            for row in reader:
                try:day=date.fromisoformat(row['date']).isoformat()
                except ValueError as error:raise ResearchError('面板日期格式无效') from error
                if before and day>=before:continue
                if through and day>through:continue
                try:session=int(row['session'])
                except ValueError as error:raise ResearchError('session 必须为连续交易日序号') from error
                if session<0 or (day in sessions and sessions[day]!=session):raise ResearchError('同一天的 session 不一致或为负')
                sessions[day]=session
                if row['available_date']!=day:raise ResearchError('本地日频协议只接受同日已公布的 PIT 快照；不回填迟到数据')
                if row['eligible'] not in ('0','1') or not row['asset']:raise ResearchError('eligible 必须是历史 0/1 标志，asset 不可为空')
                key=(day,row['asset'])
                if key in rows:raise ResearchError('面板日期/资产重复')
                values={k:(row[k] if k=='subindustry' else number(row[k])) for k in self.columns}
                if values.get('close') is not None and values['close']<=0:raise ResearchError('close 必须为正数')
                rows[key]=(values,row['eligible']=='1');dates.add(day);assets.add(row['asset'])
                if len(rows)>100_000:raise ResearchError('本地面板行数上限 100000')
        self.dates=sorted(dates);self.assets=sorted(assets)
        if not self.dates or not self.assets:raise ResearchError('本地面板为空')
        ordered_sessions=[sessions[d] for d in self.dates]
        if any(b!=a+1 for a,b in zip(ordered_sessions,ordered_sessions[1:])):raise ResearchError('交易日历存在缺口/重复；不能把多日变动当作一日标签')
        if len(self.dates)>3000 or len(self.assets)>300:raise ResearchError('日频原型最多 3000 日 / 300 资产')
        self.values={k:[[rows.get((d,a),({},False))[0].get(k) for a in self.assets] for d in self.dates] for k in self.columns}
        self.eligible=[[rows.get((d,a),({},False))[1] for a in self.assets] for d in self.dates]
        self.targets=[]
        close=self.values['close']
        for t in range(len(self.dates)-1):
            daily=[]
            for j in range(len(self.assets)):
                a,b=close[t][j],close[t+1][j]
                daily.append(number(b/a-1) if a and b else None)
                if self.eligible[t][j] and daily[-1] is None:
                    raise ResearchError('可交易资产缺少下一期价格；禁止静默丢弃退市/缺失收益')
            self.targets.append(daily)


class FormulaKernel:
    def __init__(self,panel):self.panel=panel;self.n=len(panel.dates);self.m=len(panel.assets);self.cache={}

    def compute(self,expression):
        return self.visit(ast.parse(expression,mode='eval').body)

    def visit(self,node):
        key=ast.dump(node)
        if key in self.cache:return self.cache[key]
        p=self.panel;n,m=self.n,self.m
        if isinstance(node,ast.Name):
            if node.id=='returns' and node.id not in p.values:
                close=p.values['close'];out=[[None]*m]+[[close[t][j]/close[t-1][j]-1 if close[t][j] and close[t-1][j] else None for j in range(m)] for t in range(1,n)]
            elif node.id in p.values:out=p.values[node.id]
            else:raise ResearchError('CSV 缺少公式字段: '+node.id)
        elif isinstance(node,ast.Constant):out=[[float(node.value)]*m for _ in range(n)]
        elif isinstance(node,ast.UnaryOp):
            x=self.visit(node.operand);sign=-1 if isinstance(node.op,ast.USub) else 1
            out=[[None if v is None else v*sign for v in row] for row in x]
        elif isinstance(node,ast.BinOp):
            a,b=self.visit(node.left),self.visit(node.right)
            def apply(x,y):
                if x is None or y is None:return None
                if isinstance(node.op,ast.Add):return x+y
                if isinstance(node.op,ast.Sub):return x-y
                if isinstance(node.op,ast.Mult):return x*y
                return x/y if y else None
            out=[[apply(x,y) for x,y in zip(ar,br)] for ar,br in zip(a,b)]
        elif isinstance(node,ast.Call):
            name=node.func.id;x=self.visit(node.args[0]);out=[[None]*m for _ in range(n)]
            if name=='rank':
                out=[rank([v if p.eligible[t][j] else None for j,v in enumerate(row)]) for t,row in enumerate(x)]
            elif name in ('abs','sign','log','sqrt','inverse','signed_power'):
                power=self.visit(node.args[1])[0][0] if name=='signed_power' else None
                def unary(v):
                    if v is None:return None
                    if name=='abs':return abs(v)
                    if name=='sign':return (v>0)-(v<0)
                    if name=='log':return math.log(v) if v>0 else None
                    if name=='sqrt':return math.sqrt(v) if v>=0 else None
                    if name=='inverse':return 1/v if v else None
                    return math.copysign(abs(v)**power,v) if v or power>=0 else None
                for t in range(n):
                    for j in range(m):
                        try:out[t][j]=unary(x[t][j])
                        except (OverflowError,ValueError):pass
            elif name in ('group_rank','group_neutralize'):
                groups=self.visit(node.args[1])
                for t in range(n):
                    buckets={}
                    for j,v in enumerate(x[t]):
                        if v is not None and p.eligible[t][j] and groups[t][j] is not None:buckets.setdefault(groups[t][j],[]).append(j)
                    for indices in buckets.values():
                        values=[x[t][j] for j in indices]
                        values=rank(values) if name=='group_rank' else [v-stats.mean(values) for v in values]
                        for j,v in zip(indices,values):out[t][j]=v
            elif name.startswith('ts_'):
                pair=name in ('ts_corr','ts_covariance')
                window=int(self.visit(node.args[2] if pair else node.args[1])[0][0])
                y=self.visit(node.args[1]) if pair else None
                for t in range(n):
                    for j in range(m):
                        if name in ('ts_delay','ts_delta'):
                            if t>=window:
                                a,b=x[t][j],x[t-window][j]
                                out[t][j]=b if name=='ts_delay' else a-b if a is not None and b is not None else None
                            continue
                        if t+1<window:continue
                        values=[x[i][j] for i in range(t-window+1,t+1)]
                        if any(v is None for v in values):continue
                        mean=stats.mean(values);sd=stats.pstdev(values)
                        if name=='ts_mean':value=mean
                        elif name=='ts_sum':value=sum(values)
                        elif name=='ts_std_dev':value=sd
                        elif name=='ts_rank':value=rank(values)[-1]
                        elif name=='ts_zscore':value=(values[-1]-mean)/sd if sd else None
                        elif name=='ts_decay_linear':value=sum((i+1)*v for i,v in enumerate(values))/(window*(window+1)/2)
                        elif pair:
                            other=[y[i][j] for i in range(t-window+1,t+1)]
                            value=(corr(values,other) if name=='ts_corr' else sum((a-mean)*(b-stats.mean(other)) for a,b in zip(values,other))/window) if all(v is not None for v in other) else None
                        else:raise ResearchError('本地尚未实现该算子: '+name)
                        out[t][j]=value
            else:raise ResearchError('本地尚未实现该算子: '+name)
        else:raise ResearchError('本地 DSL 结构不支持')
        # Group strings remain symbolic, all numerical overflow becomes missing evidence.
        out=[[v if isinstance(v,str) or v is None or math.isfinite(v) else None for v in row] for row in out]
        self.cache[key]=out;return out


def summary(records,annualization=252):
    values=[r['net_return'] for r in records];ics=[r['rank_ic'] for r in records if r['rank_ic'] is not None]
    if not values:return {'observations':0,'net_sharpe':None,'mean_rank_ic':None,'coverage':0,'mean_turnover':None,'worst_year_sharpe':None}
    def sharpe(v):
        sd=stats.stdev(v) if len(v)>1 else 0
        return stats.mean(v)/sd*math.sqrt(annualization) if sd>1e-12 else None
    years={}
    nav=peak=1.;maxdd=0
    for r in records:
        nav*=1+r['net_return']
        if not math.isfinite(nav):raise ResearchError('本地收益/净值溢出，需检查价格调整和数据质量')
        peak=max(peak,nav);maxdd=min(maxdd,nav/peak-1)
        years.setdefault(r['date'][:4],[]).append(r['net_return'])
    ys=[sharpe(v) for v in years.values()];ys=[v for v in ys if v is not None]
    return {'observations':len(values),'net_sharpe':sharpe(values),'mean_rank_ic':stats.mean(ics) if ics else None,
        'ic_observations':len(ics),'coverage':sum(r['invested'] for r in records)/len(records),
        'mean_turnover':stats.mean(r['turnover'] for r in records),'total_return':nav-1,'max_drawdown':maxdd,
        'worst_year_sharpe':min(ys) if ys else None}


def evaluate_schedule(panel,signals,schedule,plan):
    previous={};records=[]
    delay=plan['delay'];cost=plan['fee_bps']/10000
    for t,identifier in schedule:
        jdate=t-delay
        sig=signals[identifier][jdate] if identifier is not None and jdate>=0 else [None]*len(panel.assets)
        eligible=[j for j,v in enumerate(sig) if v is not None and panel.eligible[t][j]]
        weights={}
        ic=None
        if len(eligible)>=plan['min_assets']:
            ranks=rank([sig[j] for j in eligible]);mean=stats.mean(ranks);gross=sum(abs(v-mean) for v in ranks)
            if gross>0:
                weights={j:(v-mean)/gross for j,v in zip(eligible,ranks)}
                ic=corr(ranks,rank([panel.targets[t][j] for j in eligible]))
        turnover=sum(abs(weights.get(j,0)-previous.get(j,0)) for j in weights.keys()|previous.keys())
        raw=sum(w*panel.targets[t][j] for j,w in weights.items())
        net=raw-turnover*cost
        if net<=-1:raise ResearchError('本地组合净值耗尽；不能继续计算')
        records.append({'date':panel.dates[t],'label_end':panel.dates[t+1],'selected_id':identifier,'net_return':net,
            'gross_return':raw,'turnover':turnover,'rank_ic':ic,'invested':bool(weights),'signal_date':panel.dates[jdate] if jdate>=0 else None})
        # Drift weights through the return interval before the next rebalance.
        previous={j:w*(1+panel.targets[t][j])/(1+net) for j,w in weights.items()}
    return {'metrics':summary(records,plan.get('annualization',252)),'daily':records}


def validate_plan(plan):
    if plan.get('holdout_mode','single_selected') not in ('single_selected','directed_fixed_comparisons_v1'):
        raise ResearchError('未知留出模式')
    if plan.get('data_permission_confirmed') is not True:raise ResearchError('须由数据负责人确认数据研究权限')
    for name in ('study_id','dataset_id','data_source','point_in_time_review'):
        if not isinstance(plan.get(name),str) or not plan[name].strip():raise ResearchError('缺少 '+name)
    if plan['dataset_id']!=plan['dataset_id'].strip() or len(plan['dataset_id'])>200:raise ResearchError('dataset_id 必须是稳定、无首尾空白的标识，最多 200 字符')
    if plan.get('validation_protocol',registry.PROTOCOL)!=registry.PROTOCOL:raise ResearchError('不支持的最终留出协议')
    for name in ('discovery_start','holdout_start','holdout_end'):date.fromisoformat(plan[name])
    if not plan['discovery_start']<plan['holdout_start']<plan['holdout_end']:raise ResearchError('发现期与留出期日期重叠或无效')
    for key in ('train_min_days','test_days','embargo_days','delay','min_assets','min_train_observations'):
        if type(plan.get(key))is not int or plan[key]<1:raise ResearchError('无效正整数 '+key)
    if plan['delay']<1 or plan['embargo_days']<1:raise ResearchError('日频一日标签必须保留延迟和隔离间隔')
    for key in ('fee_bps','min_train_ic','min_train_sharpe','max_train_turnover','min_train_coverage'):
        if number(plan.get(key)) is None:raise ResearchError('无效参数 '+key)
    if plan['fee_bps']<0 or not -1<=plan['min_train_ic']<=1 or not 0<=plan['min_train_coverage']<=1 or plan['max_train_turnover']<0:raise ResearchError('本地评价参数越界')
    if plan.get('label_horizon_days',1)!=1:raise ResearchError('当前仅实现一日收益标签，禁止默默使用重叠多日标签')
    annual=plan.get('annualization',252)
    if type(annual)is not int or not 1<=annual<=366:raise ResearchError('annualization 必须为 1..366 的整数')
    minimum=plan.get('min_holdout_observations',20)
    if type(minimum)is not int or minimum<2:raise ResearchError('min_holdout_observations 至少为 2')
    if plan['min_assets']<3 or plan['train_min_days']<plan['min_train_observations']:raise ResearchError('训练窗/横截面规模不足以执行已声明的评价')
    return plan


def prepare(panel,candidates,validator):
    signals={}
    for c in candidates:
        valid=validator.validate(c['expression']);identifier=valid.fingerprint
        if identifier not in signals:signals[identifier]=FormulaKernel(panel).compute(c['expression'])
    return signals


def choose(panel,signals,indices,plan):
    rows=[]
    for identifier in signals:
        metrics=evaluate_schedule(panel,signals,[(i,identifier) for i in indices],plan)['metrics']
        valid=(metrics['observations']>=plan['min_train_observations'] and metrics['net_sharpe'] is not None and metrics['mean_rank_ic'] is not None and
            metrics['net_sharpe']>=plan['min_train_sharpe'] and metrics['mean_rank_ic']>=plan['min_train_ic'] and
            metrics['mean_turnover']<=plan['max_train_turnover'] and metrics['coverage']>=plan['min_train_coverage'])
        rows.append({'id':identifier,'eligible':valid,'metrics':metrics})
    eligible=[r for r in rows if r['eligible']]
    eligible.sort(key=lambda r:(-(r['metrics']['worst_year_sharpe'] if r['metrics']['worst_year_sharpe'] is not None else -1e6),
        -r['metrics']['mean_rank_ic'],r['metrics']['mean_turnover'],r['id']))
    return (eligible[0]['id'] if eligible else None),rows


def ledger(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30,isolation_level=None)
    db.execute('CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY,manifest_hash TEXT NOT NULL,manifest TEXT NOT NULL,state TEXT NOT NULL,created TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS final_reveals(data_key TEXT PRIMARY KEY,study_id TEXT,state TEXT,created TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS final_reports(study_id TEXT PRIMARY KEY,payload TEXT NOT NULL,created TEXT NOT NULL)')
    return db


def freeze(panel_path,candidates_path,plan_path,config,ledger_path,output):
    candidates=json.loads(Path(candidates_path).read_text(encoding="utf-8"));plan=validate_plan(json.loads(Path(plan_path).read_text(encoding="utf-8")))
    if not isinstance(candidates,list) or not 1<=len(candidates)<=20 or not all(isinstance(c,dict) and isinstance(c.get('expression'),str) for c in candidates):raise ResearchError('预注册需 1..20 个公式')
    if plan.get('holdout_mode')=='directed_fixed_comparisons_v1':
        from .directed import fixed_candidates
        if candidates!=fixed_candidates():raise ResearchError('定向留出必须冻结完整六公式设计与四个比较对，禁止事后筛选')
    validator=Validator(config['catalog_data'],config['scope'],config['rules'])
    if plan['delay']!=config['scope']['delay']:raise ResearchError('本地 Delay 必须与冻结研究范围一致')
    panel=Panel(panel_path,before=plan['holdout_start']);signals=prepare(panel,candidates,validator)
    indices=[i for i,d in enumerate(panel.dates[:-1]) if d>=plan['discovery_start']]
    if len(indices)<plan['train_min_days']:raise ResearchError('发现期样本不足')
    selected,training=choose(panel,signals,indices,plan)
    registry_path=Path(config.get('validation',{}).get('holdout_registry',Path(config['root'])/'state/holdout-registry.sqlite3')).resolve()
    if registry_path in (Path(ledger_path).resolve(),Path(config['database']).resolve()):raise ResearchError('最终留出登记账本必须独立于研究/实验账本')
    register,registry_id=registry.connect(registry_path,create=True);register.close()
    content={'version':'0.3.3','created_at':now(),'panel_path':str(Path(panel_path).resolve()),'panel_sha256':file_hash(panel_path),
        'plan':plan,'candidates':candidates,'candidate_file_sha256':file_hash(candidates_path),
        'code_hash':code_hash(),'catalog':config['catalog_data'],'scope':config['scope'],'rules':config['rules'],
        'frozen_selected_id':selected,'training_only_scores':training,'synthetic':bool(plan.get('synthetic',False)),
        'independent_holdout_validated':False,
        'holdout_registry':{'path':str(registry_path),'registry_id':registry_id,'protocol':registry.PROTOCOL}}
    content['manifest_hash']=digest(content)
    target=Path(output)
    if target.exists():raise ResearchError('预注册文件已存在，不覆盖')
    db=ledger(ledger_path)
    try:
        db.execute('BEGIN IMMEDIATE')
        legacy=db.execute('''SELECT s.manifest FROM final_reveals r JOIN studies s ON s.id=r.study_id''').fetchall()
        if any('holdout_registry' not in json.loads(row[0]) for row in legacy):
            imported=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_reveal_imports'").fetchone()
            if not imported or not db.execute('SELECT 1 FROM legacy_reveal_imports WHERE registry_id=? AND dataset_id=?',(registry_id,plan['dataset_id'])).fetchone():
                raise ResearchError('旧账本含已暴露留出；先执行 validation-import-legacy，不能通过升级重置权限')
        if db.execute('SELECT 1 FROM studies WHERE id=?',(plan['study_id'],)).fetchone():raise ResearchError('study_id 已预注册；不能重置最终留出实验')
        db.execute('INSERT INTO studies VALUES(?,?,?,?,?)',(plan['study_id'],content['manifest_hash'],dumps(content),'frozen',now()))
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x', encoding="utf-8") as out:json.dump(content,out,ensure_ascii=False,indent=2,allow_nan=False)
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()
    return content


def verified_manifest(path,ledger_path):
    content=json.loads(Path(path).read_text(encoding="utf-8"));given=content.get('manifest_hash')
    if content.get('version') not in ('0.3.1','0.3.2','0.3.3') or 'holdout_registry' not in content:raise ResearchError('旧版预注册不具备独立揭示登记；保留旧报告，导入已揭示记录后重新预注册未使用数据')
    if given!=digest({k:v for k,v in content.items() if k!='manifest_hash'}):raise ResearchError('预注册内容哈希不匹配')
    if content['code_hash']!=code_hash():raise ResearchError('预注册后评价代码已改变')
    if file_hash(content['panel_path'])!=content['panel_sha256']:raise ResearchError('预注册后数据内容已改变')
    db=ledger(ledger_path)
    try:row=db.execute('SELECT manifest_hash FROM studies WHERE id=?',(content['plan']['study_id'],)).fetchone()
    finally:db.close()
    if not row or row[0]!=given:raise ResearchError('未找到匹配的预注册记录')
    return content


def walk_forward(manifest_path,ledger_path):
    m=verified_manifest(manifest_path,ledger_path);plan=m['plan']
    panel=Panel(m['panel_path'],before=plan['holdout_start'])
    validator=Validator(m['catalog'],m['scope'],m['rules']);signals=prepare(panel,m['candidates'],validator)
    indices=[i for i,d in enumerate(panel.dates[:-1]) if d>=plan['discovery_start']]
    folds=[];schedule=[]
    start=plan['train_min_days']+plan['embargo_days']+1
    for position in range(start,len(indices),plan['test_days']):
        train=indices[:position-plan['embargo_days']-1];test=indices[position:position+plan['test_days']]
        selected,scores=choose(panel,signals,train,plan)
        folds.append({'train_start':panel.dates[train[0]],'last_train_label_end':panel.dates[train[-1]+1],
            'test_start':panel.dates[test[0]],'test_end':panel.dates[test[-1]+1],
            'selected_id':selected,'training_only_scores':scores})
        schedule.extend((i,selected) for i in test)
    if not folds:raise ResearchError('日期范围不足以形成 Walk-Forward 窗口')
    result=evaluate_schedule(panel,signals,schedule,plan)
    return {'study_id':plan['study_id'],'mode':'walk_forward_discovery_diagnostic','folds':folds,**result,
        'synthetic':m['synthetic'],'independent_holdout_validated':False,'final_holdout_read':False,
        'notice':'每折只在历史训练窗选公式；反复阅读此报告后，这些测试窗也属于研究数据'}


def final_holdout(manifest_path,ledger_path):
    m=verified_manifest(manifest_path,ledger_path);plan=m['plan']
    comparison=plan.get('holdout_mode')=='directed_fixed_comparisons_v1'
    if not m['frozen_selected_id'] and not comparison:raise ResearchError('训练期没有合格公式，不揭示留出期')
    location=m['holdout_registry']
    register,_=registry.connect(location['path'],expected_id=location['registry_id'])
    db=ledger(ledger_path)
    key=None
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT state FROM studies WHERE id=?',(plan['study_id'],)).fetchone()
        if row[0]!='frozen':raise ResearchError('该最终留出已揭示或正在执行，禁止重复优化/重试')
        # The separate register survives new study IDs and new study DBs. Reserve
        # BEFORE parsing any holdout values; file bytes are only an integrity check.
        key=registry.reserve(register,plan['dataset_id'],plan['holdout_start'],plan['holdout_end'],plan['study_id'],ledger_path)
        db.execute('INSERT INTO final_reveals VALUES(?,?,?,?)',(key,plan['study_id'],'revealing',now()))
        db.execute("UPDATE studies SET state='revealing' WHERE id=?",(plan['study_id'],));db.execute('COMMIT')
        # Reserve the one-shot reveal BEFORE parsing holdout observations.
        try:
            semantic=registry.semantic_hash(m['panel_path'],plan['holdout_start'],plan['holdout_end'])
            registry.bind_semantic_hash(register,key,semantic)
            panel=Panel(m['panel_path'],through=plan['holdout_end'])
            validator=Validator(m['catalog'],m['scope'],m['rules'])
            selected_candidates=m['candidates'] if comparison else [c for c in m['candidates'] if validator.validate(c['expression']).fingerprint==m['frozen_selected_id']]
            signals=prepare(panel,selected_candidates,validator)
            indices=[i for i,d in enumerate(panel.dates[:-1]) if plan['holdout_start']<=d and panel.dates[i+1]<=plan['holdout_end']]
            if len(indices)<plan.get('min_holdout_observations',20):raise ResearchError('留出期观测不足')
            if comparison:
                from .directed import fixed_candidates
                if m['candidates']!=fixed_candidates():raise ResearchError('定向验证公式设计不一致')
                metrics={c['key']:evaluate_schedule(panel,signals,[(i,validator.validate(c['expression']).fingerprint) for i in indices],plan)['metrics'] for c in m['candidates']}
                pairs=[]
                for c in m['candidates']:
                    if not c['parent_key']:continue
                    a,b=metrics[c['parent_key']],metrics[c['key']]
                    delta={k:(b[k]-a[k] if a[k] is not None and b[k] is not None else None)
                           for k in ('net_sharpe','mean_turnover','mean_rank_ic')}
                    pairs.append({'baseline':c['parent_key'],'variant':c['key'],'change':c['change'],'delta':delta})
                result={'metrics_by_candidate':metrics,'prespecified_comparisons':pairs,'selected_after_validation':False,
                    'decision':'report_all_prespecified_pairs_no_posthoc_winner','feedback_to_agent':False}
            else:
                result=evaluate_schedule(panel,signals,[(i,m['frozen_selected_id']) for i in indices],plan)
        except BaseException:
            db.execute("UPDATE studies SET state='failed_after_reveal' WHERE id=?",(plan['study_id'],))
            register.execute("UPDATE reveal_claims SET state='failed_after_reveal' WHERE claim_key=? AND state<>'blocked_duplicate'",(key,))
            raise
        final={'study_id':plan['study_id'],'mode':'one_shot_directed_comparison' if comparison else 'one_shot_final_time_holdout','selected_id':None if comparison else m['frozen_selected_id'],**result,
            'synthetic':m['synthetic'],'protocol_completed':True,'holdout_revealed_once':True,
            'dataset_id':plan['dataset_id'],'holdout_protocol':registry.PROTOCOL,
            'holdout_claim_key':key,'holdout_semantic_sha256':semantic,
            'independent_holdout_validated':False,'brain_equivalent':False,
            'notice':'时间隔离协议已执行；数据时点、幸存者偏差、独立性和可交易性仍需外部审查；结果不回流给 Research Agent'}
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO final_reports VALUES(?,?,?)',(plan['study_id'],dumps(final),now()))
        db.execute("UPDATE studies SET state='completed' WHERE id=?",(plan['study_id'],))
        db.execute("UPDATE final_reveals SET state='completed' WHERE data_key=?",(key,));db.execute('COMMIT')
        register.execute("UPDATE reveal_claims SET state='completed' WHERE claim_key=?",(key,))
    finally:
        if db.in_transaction:db.execute('ROLLBACK')
        db.close();register.close()
    return final


def saved_final_report(study_id,ledger_path):
    db=ledger(ledger_path)
    try:row=db.execute('SELECT payload FROM final_reports WHERE study_id=?',(study_id,)).fetchone()
    finally:db.close()
    if not row:raise ResearchError('此研究没有已完成的最终报告')
    return json.loads(row[0])
