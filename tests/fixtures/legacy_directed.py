"""A fixed six-formula experiment; no model-selected expansion or score chasing."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from alpharesearch.common import ResearchError, digest, dumps, now, snapshot
from alpharesearch.engine import Engine
from alpharesearch.memory import Memory
from alpharesearch.validator import Validator

PROTOCOL='directed-reversal-vwap-v1'
HOLDOUT_MODE='directed_fixed_comparisons_v1'


def fixed_candidates():
    specs=[('reversal_raw5','reversal',None,{'normalize_return':False,'lookback':5}),
        ('reversal_normalized5','reversal','reversal_raw5',{'normalize_return':True,'lookback':5}),
        ('reversal_normalized10','reversal','reversal_normalized5',{'normalize_return':True,'lookback':10}),
        ('vwap_smooth1','vwap',None,{'smoothing_sessions':1}),
        ('vwap_smooth3','vwap','vwap_smooth1',{'smoothing_sessions':3}),
        ('vwap_smooth5','vwap','vwap_smooth3',{'smoothing_sessions':5})]
    result=[]
    for key,family,parent,parameters in specs:
        if family=='reversal':
            w=parameters['lookback']
            core=f'ts_delta(close, {w})'
            if parameters['normalize_return']:core+=f' / ts_delay(close, {w})'
            expression=f'-rank({core})'
            hypothesis='短期冲击反转：检验收益率归一化与窗口长度；趋势延续可能使假设失效。'
        else:
            expression=f'rank(ts_mean((vwap - close) / vwap, {parameters["smoothing_sessions"]}))'
            hypothesis='相对 VWAP 偏离：仅延长信号平滑窗口以检验换手下降；可能损失短期信息。'
        old=next((c for c in result if c['key']==parent),None)
        changes={k:{'before':old['parameters'][k],'after':v} for k,v in parameters.items()
                 if old and old['parameters'][k]!=v}
        result.append({'key':key,'economic_family':family,'parent_key':parent,'parameters':parameters,
            'change':changes,'expression':expression,'hypothesis':hypothesis,'source':PROTOCOL+' (unverified hypothesis)'})
    return result


def create_plan(config,study_id,output,candidates_output):
    from alpharesearch.probe import _write_fresh
    if not isinstance(study_id,str) or not study_id.strip() or len(study_id)>120:
        raise ResearchError('提供稳定、非空的 study_id')
    if Path(output).exists() or Path(candidates_output).exists():raise ResearchError('预注册输出已存在；不覆盖原计划')
    if Path(output).resolve()==Path(candidates_output).resolve():raise ResearchError('计划与公式输出必须不同')
    validator=Validator(config['catalog_data'],config['scope'],config['rules'])
    candidates=fixed_candidates()
    for c in candidates:validator.validate(c['expression'])
    if config['rules']['max_simulations_per_run']<6:raise ResearchError('定向设计需最多 6 次预算（无隐含扰动）；不能扩大 program.md 预算')
    value={'protocol':PROTOCOL,'study_id':study_id,'created_at':now(),'scope':config['scope'],
        'snapshot_sha256':digest(snapshot(config)),'max_simulations':6,'automatic_sensitivity':False,
        'candidates':candidates,'independent_validation':{'holdout_mode':HOLDOUT_MODE,
            'fixed_pairs':[[c['parent_key'],c['key']] for c in candidates if c['parent_key']],
            'decision':'report_all_prespecified_pairs_no_posthoc_winner',
            'feedback_to_agent':False},'empirical_effectiveness_verified':False}
    value['plan_sha256']=digest(value)
    _write_fresh(output,value)
    _write_fresh(candidates_output,candidates)
    return value


def load_plan(path,config):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if (value.get('protocol')!=PROTOCOL or value.get('plan_sha256')!=digest({k:v for k,v in value.items() if k!='plan_sha256'})
        or value.get('candidates')!=fixed_candidates() or value.get('max_simulations')!=6
        or value.get('automatic_sensitivity') is not False
        or value.get('snapshot_sha256')!=digest(snapshot(config)) or value.get('scope')!=config['scope']):
        raise ResearchError('定向计划/代码/目录/规则已改变；不得静默增加实验或替换参数')
    return value


def execute_plan(config,plan_path,output,simulator=None,max_new_simulations=None,confirm_preview=None):
    """Stable study ID binds one run and its spent budget across restarts."""
    plan=load_plan(plan_path,config)
    from alpharesearch.preview import preview,confirm
    estimate=preview(config,plan['candidates'],plan=plan)
    allowed=confirm(config,estimate,max_new_simulations,confirm_preview)
    memory=Memory(config['database'])
    try:
        def bind():
            row=memory.db.execute('SELECT * FROM directed_studies WHERE study_id=?',(plan['study_id'],)).fetchone()
            if row:
                if row['plan_hash']!=plan['plan_sha256']:raise ResearchError('study_id 已绑定另一计划')
                return row['run_id']
            run_id=uuid.uuid4().hex[:16]
            snap=snapshot(config)
            memory.db.execute('INSERT INTO runs(id,created,snapshot,snapshot_hash) VALUES(?,?,?,?)',(run_id,now(),dumps(snap),digest(snap)))
            memory.db.execute('INSERT INTO directed_studies VALUES(?,?,?,?)',(plan['study_id'],run_id,plan['plan_sha256'],now()))
            memory.event(run_id,'directed_plan_frozen',plan)
            return run_id
        run_id=memory.transaction(bind)
    finally:memory.close()
    engine=Engine(config,run_id,simulator,simulation_limit=min(plan['max_simulations'],estimate['simulations_already_reserved']+allowed))
    try:
        engine.memory.transaction(lambda:engine.memory.event(run_id,'research_arm',{'agent':'fixed_directed','plan_sha256':plan['plan_sha256']}))
        engine.memory.transaction(lambda:engine.memory.event(run_id,'creation_budget_confirmed',
            {'preview_sha256':estimate['preview_sha256'],'max_new_simulations':allowed,'reserved_before':estimate['simulations_already_reserved']}))
        for c in plan['candidates']:
            # Add once; do not use propose(), which creates automatic neighbors.
            events=[json.loads(r[0]) for r in engine.memory.db.execute(
                "SELECT payload FROM events WHERE run_id=? AND kind='directed_trial'",(run_id,))]
            known=next((r for r in events if r['key']==c['key']),None)
            if known:
                trial_id=known['trial_id']
            else:
                engine.memory.consume(run_id,'proposals',config['rules']['max_proposals_per_run'])
                valid=engine.validator.validate(c['expression'])
                trial_id,_=engine.memory.add_trial(run_id,c,valid,engine.context)
                engine.memory.add_intent(run_id,trial_id,c['hypothesis'],c['source'])
                engine.memory.transaction(lambda:engine.memory.event(run_id,'directed_trial',
                    {'key':c['key'],'trial_id':trial_id,'parent_key':c['parent_key'],'parameters':c['parameters'],'change':c['change']}))
            trial=engine.memory.trial(trial_id)
            if trial['run_id']!=run_id and trial['state']!='completed':
                raise ResearchError('共享公式尚未完成；先只读恢复原 run，不重复创建')
            if trial['state']=='queued' and engine.memory.run(run_id)['simulations']>=engine.simulation_limit:
                break
            engine.execute(trial_id)
        report=engine.write_report(output)
        return {'run_id':run_id,'study_id':plan['study_id'],'counts':report['counts'],
            'plan_sha256':plan['plan_sha256'],'automatic_sensitivity':False,
            'invocation_max_new_simulations':allowed,'preview_sha256':estimate['preview_sha256'],
            'plan_completed':all(any(t['expression']==c['expression'] and t['state']=='completed' for t in report['trials']) for c in plan['candidates']),
            'independent_validation_completed':False,'report':str(output)}
    finally:engine.memory.close()
