#!/usr/bin/env python3
"""Offline full regression, independent interpreters per test module.

No real LLM/BRAIN/Submit traffic: socket connections are denied in each
worker, and account/model credentials are removed. Emits an auditable log and
JSON summary with no raw research response bodies.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from verification.v08_module_matrix import WORKER


def run(root,report_dir,timeout,jobs=4):
    root=Path(root).resolve(); report_dir=Path(report_dir).resolve()
    report_dir.mkdir(parents=True,exist_ok=True)
    names=sorted(p.name for p in (root/'tests').glob('test_*.py'))
    if len(names)<20:
        raise RuntimeError('Expected full AlphaResearch source test directory')
    env={key:val for key,val in os.environ.items()
         if not key.startswith('BRAIN_') and key not in (
             'LLM_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY',
             'QINIU_API_KEY','GITHUB_TOKEN')}
    def run_one(name):
        start=time.monotonic()
        try:
            child=subprocess.run([sys.executable,'-u','-c',WORKER,name],
                                 cwd=root,env=env,text=True,capture_output=True,
                                 timeout=timeout)
            output=child.stdout+'\n'+child.stderr
            match=re.search(r'OFFLINE_QA_MARKER (\d+) (\d+) (\d+)',output)
            n=int(match[1]) if match else 0
            passed=bool(match) and child.returncode==0 and int(match[2])==int(match[3])==0
            state='PASS' if passed else 'FAIL'
        except subprocess.TimeoutExpired:
            output=f'Worker timed out after {timeout} seconds; no assumed PASS.\n'
            n=0;state='TIMEOUT'
        row={'module':name,'state':state,'tests_run':n,
             'duration_seconds':round(time.monotonic()-start,3)}
        return row,f'=== {name} {state} ({n} tests) ===\n{output}\n'

    # Independent workers can run concurrently: every test module uses its
    # own interpreter and the tests' own temporary database directories.
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        entries=list(pool.map(run_one,names))
    rows=[row for row,_ in entries]
    parts=[log for _,log in entries]
    for row in rows:
        print(f"{row['module']}: {row['state']} ({row['tests_run']})",flush=True)
    from scripts.release import verify_sources
    inventory_count=verify_sources(root)
    log=''.join(parts)
    log_path=report_dir/'full-offline-regression.log'
    log_path.write_text(log,encoding='utf-8')
    receipt={'status':'PASS' if all(row['state']=='PASS' for row in rows) else 'FAIL',
             'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
             'source_path':str(root),'source_manifest_files':inventory_count,
             'test_modules':len(rows),'tests_run':sum(row['tests_run'] for row in rows),
             'offline_network_connect_denied':True,
             'real_llm_requests_executed_by_this_runner':0,
             'real_brain_simulations':0,'submits':0,
             'scripted_results_are_not_genuine_llm_evidence':True,
             'log_sha256':hashlib.sha256(log.encode('utf-8')).hexdigest(),
             'modules':rows}
    (report_dir/'full-offline-regression.json').write_text(
        json.dumps(receipt,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default='.')
    parser.add_argument('--report-dir',required=True)
    parser.add_argument('--timeout-seconds',type=int,default=90)
    parser.add_argument('--jobs',type=int,default=4)
    args=parser.parse_args()
    if not 10<=args.timeout_seconds<=300:
        parser.error('Set a bounded timeout between 10 and 300 seconds')
    if not 1<=args.jobs<=8:
        parser.error('Use between one and eight isolated workers')
    receipt=run(args.repo,args.report_dir,args.timeout_seconds,args.jobs)
    print('FINAL',receipt['status'],receipt['tests_run'],flush=True)
    return 0 if receipt['status']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
