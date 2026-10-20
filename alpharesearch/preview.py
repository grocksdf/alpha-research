"""Offline read-only estimates and explicit, bounded live creation approval."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .common import ResearchError,digest,research_context,snapshot
from .scheduling import blocking_tasks
from .validator import Validator


def research_candidates(config,agent,path=None):
    if agent=='llm':return None
    if agent=='file':
        if not path:raise ResearchError('--agent file 需要 --candidates')
        from .batch import read_candidates
        return read_candidates(path)
    if path:raise ResearchError('--candidates 只适用于 file agent')
    return json.loads((Path(config['root'])/'examples/baseline.json').read_text(encoding='utf-8'))


def preview(config,candidates,*,plan=None,papers_path=None):
    """No Memory(), run creation, schema migration, credentials or HTTP calls."""
    validator=Validator(config['catalog_data'],config['scope'],config['rules'])
    expressions={}
    if candidates is not None:
        for c in candidates[:config['rules']['max_proposals_per_run']]:
            variants=[c['expression']]
            if plan is None:variants+=validator.neighbors(c['expression'])
            for expression in variants:
                valid=validator.validate(expression)
                expressions.setdefault(valid.fingerprint,{'expression':expression,'fingerprint':valid.fingerprint})
    data={'version':'0.4.1','mode':config['mode'],'scope':config['scope'],
        'snapshot_sha256':digest(snapshot(config)),'context':research_context(config),
        'input_sha256':digest(candidates),'papers_sha256':digest(Path(papers_path).read_text(encoding='utf-8')) if papers_path else None,
        'study_id':plan['study_id'] if plan else None,'plan_sha256':plan['plan_sha256'] if plan else None,
        'run_id':None,'simulations_already_reserved':0,
        'simulation_budget':min(config['rules']['max_simulations_per_run'],plan['max_simulations'] if plan else config['rules']['max_simulations_per_run']),
        'items':[],'remote_blockers':[],'shared_pending_trials':[],
        'schema_migration_required':False,
        'simulation_created':False,'network_requests':0,'database_mutated':False}
    db=None
    try:
        path=Path(config['database'])
        if path.exists():
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'runs','trials','tasks'}<=tables:raise ResearchError('已有数据库 schema 无法核实，不能按空库估算')
            data['schema_migration_required']='remote_state' not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')}
            if plan and 'directed_studies' in tables:
                row=db.execute('SELECT * FROM directed_studies WHERE study_id=?',(plan['study_id'],)).fetchone()
                if row:
                    if row['plan_hash']!=plan['plan_sha256']:raise ResearchError('study_id 已绑定不同计划')
                    data['run_id']=row['run_id']
                    data['simulations_already_reserved']=db.execute('SELECT simulations FROM runs WHERE id=?',(row['run_id'],)).fetchone()[0]
            if config['mode']=='live':
                account=config['catalog_data'].get('account_id')
                if not account:raise ResearchError('live 预览缺少账户字段目录身份')
                data['remote_blockers']=blocking_tasks(db,digest(str(account)),config['brain']['base_url'])
        new=0
        for fingerprint,item in expressions.items():
            found=db.execute('''SELECT t.id,t.run_id,q.state FROM trials t JOIN tasks q ON q.trial_id=t.id
                WHERE t.context=? AND t.fingerprint=?''',(data['context'],fingerprint)).fetchone() if db else None
            item={**item,'trial_id':found['id'] if found else None,'state':found['state'] if found else 'not_registered'}
            if found is None or (found['state']=='queued' and found['run_id']==data['run_id']):
                item['action']='create';new+=1
            elif found['state']=='completed':item['action']='reuse'
            elif found['run_id']!=data['run_id']:
                item['action']='wait_for_original_run';data['shared_pending_trials'].append(found['id'])
            else:item['action']='get_recovery_only'
            data['items'].append(item)
        remaining=max(0,data['simulation_budget']-data['simulations_already_reserved'])
        data['remaining_budget']=remaining
        data['estimate_kind']='unknown_llm_candidates_budget_upper_bound' if candidates is None else 'fixed_candidates_with_deduplication'
        data['new_simulations_needed']=None if candidates is None else new
        data['new_simulations_upper_bound']=remaining if candidates is None else min(new,remaining)
        data['budget_sufficient']=True if candidates is None else new<=remaining
        data['creation_blocked']=bool(data['remote_blockers'] or data['shared_pending_trials'] or data['schema_migration_required'])
        data['preview_sha256']=digest(data)
        return data
    except sqlite3.Error as error:
        raise ResearchError('无法只读核实实验状态；没有创建模拟') from error
    finally:
        if db:db.close()


def confirm(config,value,maximum=None,preview_sha256=None):
    """Returns the per-invocation maximum; no silent expansion after approval."""
    if maximum is not None and (type(maximum) is not int or not 0<=maximum<=value['simulation_budget']):
        raise ResearchError('--max-new-simulations 必须在协议预算范围内')
    if preview_sha256 is not None and preview_sha256!=value['preview_sha256']:
        raise ResearchError('预览已过期或输入/预算/任务状态变更；重新 --dry-run')
    expected=value['new_simulations_upper_bound']
    if config['mode']=='live' and expected:
        if maximum is None or preview_sha256 is None:
            raise ResearchError('真实新建前先 --dry-run，再提供 --confirm-preview SHA 和 --max-new-simulations N')
        if value['schema_migration_required']:raise ResearchError('先离线运行 migrate-state，再重新 --dry-run；预览不会代为迁移旧库')
        if value['creation_blocked']:raise ResearchError('账户仍有待恢复远程任务；本次只能使用只读恢复入口')
    return expected if maximum is None else maximum
