#!/usr/bin/env python3
"""Scripted three-batch v0.8.3 SQLite evidence fixture, never genuine LLM.

This verification tool imports its ONLY formula-producing fixture from tests/.
It cannot connect to any external model or BRAIN provider; no submit action.
The generated database is exclusively synthetic offline protocol evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))

from alpharesearch.autoresearch_v08 import Playbook,execute_batch,make_plan
from alpharesearch.common import load_settings
from alpharesearch.memory import Memory
from test_v083_playbook import MixedEvidenceScript


def run(output):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    dbfile=output/'AlphaResearch-v0.8.3-SCRIPTED-OFFLINE-evidence.sqlite3'
    if dbfile.exists():
        raise RuntimeError('Refuse to overwrite an existing offline evidence database')
    for name in tuple(os.environ):
        if name.startswith('BRAIN_') or name in ('LLM_API_KEY','OPENAI_API_KEY'):
            os.environ.pop(name,None)
    config=load_settings(ROOT/'config/settings.toml')
    config['database']=str(dbfile)
    config['mode']='demo'
    config['llm']['model']='offline-scripted-only'
    plan=make_plan(config,project_id='v083-3batch-offline',
                   objective='Test evidence-bound synthetic workflow, not real alpha performance',
                   max_simulations_per_batch=2)
    rounds=[]
    script_one=MixedEvidenceScript(str(dbfile),outcome='mixed')
    script_revision=MixedEvidenceScript(str(dbfile),outcome='all_valid',deferred=True)
    for i,script in enumerate((script_one,script_revision,script_revision),1):
        result=execute_batch(config,plan,adapter=script)
        rounds.append({'batch':i,'new_trials':result['new_trials'],
                       'knowledge_revisions':len(result['knowledge_revisions']),
                       'knowledge_rejected':len(result['knowledge_rejected']),
                       'pending_revisions':result['pending_critic_revisions'],
                       'memory_retrieved':result['memory_retrieved'],
                       'memory_referenced':result['memory_referenced'],
                       'memory_applied':result['memory_applied'],
                       'successful_real_llm_calls':0,
                       'market_alpha_validated':False})
    memory=Memory(str(dbfile))
    try:
        store=Playbook(memory)
        checks=store.audit('v083-3batch-offline')
        counts={table:memory.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                for table in ('v08_batches','v08_queue','v08_revisions',
                              'v08_knowledge','v08_knowledge_reviews',
                              'results','trials','v08_requests','events')}
        grades=[x[0] for x in memory.db.execute('SELECT DISTINCT evidence_grade FROM v08_knowledge')]
        unknowns=memory.db.execute("SELECT COUNT(*) FROM v08_queue WHERE state='testing'").fetchone()[0]
        if counts['v08_batches']!=3 or unknowns or any(x!='synthetic_process' for x in grades):
            raise AssertionError('Offline three-batch safety invariants failed')
    finally:
        memory.close()
    receipt={'protocol':'v083-scripted-offline-three-batch-fixture-v1',
             'genuine_llm_experiment':'NOT_RUN',
             'fixture_is_not_real_llm':True,
             'original_round3_sqlite_provided':False,
             'real_brain_simulations':0,'submits':0,
             'synthetic_not_market_performance':True,
             'rounds':rounds,'counts':counts,'grades':grades,'audit':checks,
             'database_sha256':hashlib.sha256(dbfile.read_bytes()).hexdigest(),
             'database_name':dbfile.name}
    file=output/'AlphaResearch-v0.8.3-SCRIPTED-offline-3batch-report.json'
    file.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    value=run(args.output)
    print(json.dumps({'database':value['database_name'],
                      'batches':len(value['rounds']),
                      'trials':value['counts']['trials'],
                      'knowledge':value['counts']['v08_knowledge'],
                      'real_llm_calls':0,'live_brain_calls':0},ensure_ascii=False))
