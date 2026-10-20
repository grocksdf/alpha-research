"""Owner-only forward monitoring, isolated from discovery memory and Agent tools."""
import csv
import json
import sqlite3
import uuid
from datetime import datetime,date
from pathlib import Path
from zoneinfo import ZoneInfo

from .brain import records,finite
from .common import ResearchError,digest,dumps,now
from .local_validation import summary


def today_in(zone):return datetime.now(ZoneInfo(zone)).date()


def database(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS monitors(id TEXT PRIMARY KEY,spec TEXT NOT NULL,created TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS observations(monitor_id TEXT REFERENCES monitors(id),day TEXT,value REAL,source TEXT,created TEXT,PRIMARY KEY(monitor_id,day))')
    for table in ('monitors','observations'):
        for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable forward record'); END")
    return db


def register(review_path,trial_id,start,path,zone='America/Los_Angeles',today=None):
    report=json.loads(Path(review_path).read_text(encoding="utf-8"));matches=[c for c in report['candidates'] if c['trial_id']==trial_id]
    if len(matches)!=1:raise ResearchError('候选 ID 不唯一或不存在')
    candidate=matches[0]
    if date.fromisoformat(start)<=(today or today_in(zone)):raise ResearchError('前瞻追踪起点必须晚于注册当日')
    spec={'trial_id':trial_id,'alpha_id':candidate['alpha_id'],'expression':candidate['expression'],
        'scope':report['snapshot']['scope'],'account_id':report['snapshot']['catalog'].get('account_id'),
        'start':start,'timezone':zone,'mode':report['mode'],'source_review_sha256':digest(report),
        'review_rules':report['snapshot']['policy'],'synthetic':report['mode']!='live'}
    spec['pnl_evidence_contract']=report['snapshot'].get('brain',{}).get('pnl_evidence',{})
    identifier=uuid.uuid4().hex[:16];db=database(path)
    try:db.execute('INSERT INTO monitors VALUES(?,?,?)',(identifier,dumps(spec),now()))
    finally:db.close()
    return {'monitor_id':identifier,**spec,'independent_live_performance_established':False}


def spec_for(path,monitor_id):
    db=database(path)
    try:row=db.execute('SELECT spec FROM monitors WHERE id=?',(monitor_id,)).fetchone()
    finally:db.close()
    if not row:raise ResearchError('未知追踪 ID')
    return json.loads(row['spec'])


def add_observations(path,monitor_id,values,source,today=None):
    spec=spec_for(path,monitor_id);latest=today or today_in(spec['timezone'])
    if not isinstance(source,str) or not source.strip():raise ResearchError('必须记录追踪收益来源')
    prepared=[];seen=set()
    for day,value in values:
        parsed=date.fromisoformat(day)
        if day<spec['start'] or parsed>latest:raise ResearchError('追踪日期早于冻结起点或处于未来')
        value=finite(value)
        if value is None or value<=-1:raise ResearchError('追踪收益无效')
        if day in seen:raise ResearchError('本批追踪日期重复')
        seen.add(day);prepared.append((day,value))
    if not prepared:raise ResearchError('没有新的前瞻日期；不重新模拟历史来制造追踪数据')
    db=database(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        for day,value in prepared:
            old=db.execute('SELECT value FROM observations WHERE monitor_id=? AND day=?',(monitor_id,day)).fetchone()
            if old and old['value']!=value:raise ResearchError('已归档的前瞻观测不可覆盖；差异需单独审查')
            if not old:db.execute('INSERT INTO observations VALUES(?,?,?,?,?)',(monitor_id,day,value,source,now()))
        db.execute('COMMIT')
    finally:
        if db.in_transaction:db.execute('ROLLBACK')
        db.close()
    return tracking_report(path,monitor_id)


def ingest_csv(path,monitor_id,csv_path,source):
    with Path(csv_path).open(newline='', encoding="utf-8") as file:
        reader=csv.DictReader(file)
        if not {'date','net_return'}<=set(reader.fieldnames or []):raise ResearchError('追踪 CSV 需 date,net_return')
        try:values=[(r['date'],float(r['net_return'])) for r in reader]
        except ValueError as e:raise ResearchError('追踪 CSV 含非数值收益') from e
    return add_observations(path,monitor_id,values,source)


def update_from_brain(path,monitor_id,simulator):
    from .pnl_evidence import reviewed_context,build_daily_evidence
    spec=spec_for(path,monitor_id)
    if spec['mode']!='live' or not simulator.account_ref or simulator.account_ref!=digest(str(spec['account_id'])):raise ResearchError('前瞻追踪账户不匹配或使用了测试记录')
    detail=simulator.api.wait(simulator.path('alpha',spec['alpha_id']))
    if detail.get('id')!=spec['alpha_id'] or detail.get('regular',{}).get('code')!=spec['expression'] or any(detail.get('settings',{}).get(k)!=v for k,v in spec['scope'].items()):raise ResearchError('追踪对象 ID/公式/设置已变更')
    contract=simulator.config['brain'].get('pnl_evidence',{})
    if not spec.get('pnl_evidence_contract') or digest(contract)!=digest(spec['pnl_evidence_contract']):raise ResearchError('追踪缺少冻结的 PnL 契约或契约已变更，禁止隐式复用旧序列')
    latest=today_in(spec['timezone']).isoformat()
    context=reviewed_context(simulator.config,detail,{'start_date':spec['start'],'end_date':latest})
    evidence=build_daily_evidence(simulator.api.wait(simulator.path('pnl',spec['alpha_id'])),context)
    if evidence['daily_evidence']['status']!='complete':raise ResearchError('前瞻交易日序列不完整；未将跨日期缺口差分追加为日收益')
    values=[(key.split('/')[1],v) for key,v in evidence['daily_returns'].items()]
    return add_observations(path,monitor_id,values,'BRAIN reviewed session-close cumulative cash PnL / constant capital; calendar '+context['calendar_sha256']+'; all-in costs not independently verified')


def tracking_report(path,monitor_id):
    spec=spec_for(path,monitor_id);db=database(path)
    try:rows=[dict(r) for r in db.execute('SELECT * FROM observations WHERE monitor_id=? ORDER BY day',(monitor_id,))]
    finally:db.close()
    daily=[{'date':r['day'],'net_return':r['value'],'rank_ic':None,'invested':True,'turnover':0} for r in rows]
    result=summary(daily,annualization=1)
    result['per_observation_sharpe']=result.pop('net_sharpe')
    result['annualized_sharpe']=None
    result.pop('worst_year_sharpe',None)
    result['mean_turnover']=None
    return {'monitor_id':monitor_id,'spec':spec,'metrics':result,'first_date':rows[0]['day'] if rows else None,
        'last_date':rows[-1]['day'] if rows else None,'sources':sorted({r['source'] for r in rows}),
        'status':'awaiting_observations' if not rows else 'short_track_record' if len(rows)<60 else 'observing',
        'independent_live_performance_established':False,'research_feedback_allowed':False,
        'notice':'已冻结后续观测；收益来源、漏报日期、费用语义及样本长度仍需审查；交易日历未核实时不年化，缺失日期不补零'}
