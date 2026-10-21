"""Read-only multiplicity inventory. A local DB cannot certify account-wide history."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .common import ResearchError, digest


def _object(raw):
    try:
        value=json.loads(raw)
        return value if isinstance(value,dict) else {}
    except (TypeError,ValueError):
        return {}


def inventory(database):
    path=Path(database)
    if not path.is_file():raise ResearchError('没有已有研究数据库；历史导出不会创建数据库')
    db=None
    try:
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        runs={r['id']:dict(r) for r in db.execute('SELECT id,snapshot,snapshot_hash,simulations,proposals,llm_calls FROM runs')}
        rows=[dict(r) for r in db.execute('''SELECT t.*,q.state,q.error,
             (SELECT payload FROM results r WHERE r.trial_id=t.id ORDER BY r.id DESC LIMIT 1) AS result_payload
             FROM trials t JOIN tasks q ON q.trial_id=t.id ORDER BY t.created,t.id''')]
        duplicate_events=[dict(r) for r in db.execute("SELECT id,run_id,payload FROM events WHERE kind='duplicate' ORDER BY id")]
        attempts=[];known={}
        for row in rows:
            result=_object(row['result_payload']);snap=_object(runs[row['run_id']]['snapshot'])
            account=snap.get('catalog',{})
            account=account.get('account_id') if isinstance(account,dict) else None
            candidate=result.get('alpha_id')
            attempt={'attempt_id':row['id'],'candidate_id':candidate if isinstance(candidate,str) and candidate else 'trial:'+row['id'],
                'family':row['family'] or 'unclassified','variant_sha256':row['fingerprint'] or digest(row['expression']),
                'status':row['state'],'attempt_kind':'registered_trial','run_id':row['run_id'],
                'expression':row['expression'],'account_ref':digest(str(account)) if account is not None else None,
                'snapshot_sha256':runs[row['run_id']]['snapshot_hash'],'error':row['error'],
                'result_sha256':digest(row['result_payload'])}
            attempts.append(attempt);known[row['id']]=attempt
        unresolved_duplicates=[]
        for event in duplicate_events:
            payload=_object(event['payload']);original=known.get(payload.get('existing'))
            if original is None:
                unresolved_duplicates.append(event['id']);continue
            attempts.append({**original,'attempt_id':'duplicate_event:'+str(event['id']),
                'attempt_kind':'reused_proposal','run_id':event['run_id'],
                'shared_original_trial_id':original['attempt_id']})
        return {'mode':'offline_attempt_history_inventory','reviewed':False,'complete':False,
            'includes_failed_invalid_unfinished':True,'review_note':'待负责人核实账户全部历史；本地数据库不能证明完整性',
            'source_sha256':digest({'runs':runs,'rows':rows,'duplicate_events':duplicate_events}),
            'attempts':attempts,'attempts_sha256':digest(attempts),'total_attempts':len(attempts),
            'registered_trial_count':len(rows),'cached_duplicate_proposals':len(duplicate_events),
            'unresolved_duplicate_events':unresolved_duplicates,'dsr_calibration':None,
            'run_budget_records':{rid:{k:r[k] for k in ('simulations','proposals','llm_calls')} for rid,r in runs.items()},
            'coverage':'local_database_only','external_attempts_not_known':True,
            'network_requests':0,'simulation_posts':0,'submit_posts':0,'database_mutated':False}
    except sqlite3.Error as error:
        raise ResearchError('无法只读核实旧研究历史；数据库未迁移') from error
    finally:
        if db:db.close()
