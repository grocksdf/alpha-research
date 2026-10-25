#!/usr/bin/env python3
"""Offline process-isolated complete test suite. Each original test module is
executed once in a separate interpreter with network and secrets disabled.
A PASS requires all child process exit codes and parsed unittest counts.
"""
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

WORKER=r'''
import os,sys,socket,unittest
from pathlib import Path
for name in list(os.environ):
 if name.startswith('BRAIN_') or name in ('LLM_API_KEY','OPENAI_API_KEY','GITHUB_TOKEN'):
  os.environ.pop(name,None)
class Blocked(socket.socket):
 def connect(self,*a,**k):raise AssertionError('OFFLINE_NETWORK_BLOCK')
 def connect_ex(self,*a,**k):raise AssertionError('OFFLINE_NETWORK_BLOCK')
socket.socket=Blocked
socket.create_connection=lambda *a,**k: (_ for _ in ()).throw(AssertionError('OFFLINE_NETWORK_BLOCK'))
name=sys.argv[1]
suite=unittest.TestLoader().discover(start_dir='tests',pattern=name)
result=unittest.TextTestRunner(verbosity=1).run(suite)
print('OFFLINE_QA_MARKER',result.testsRun, len(result.errors),len(result.failures),flush=True)
os._exit(0 if result.wasSuccessful() else 1)
'''

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',default='.')
    ap.add_argument('--report-dir',required=True)
    ap.add_argument('--start',type=int,default=0)
    ap.add_argument('--end',type=int,default=None)
    args=ap.parse_args()
    root=Path(args.repo).resolve();folder=Path(args.report_dir).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    from_repo=str(root)
    sys.path.insert(0,str(root))
    names=sorted(x.name for x in (root/'tests').glob('test_*.py'))
    if len(names)<20:raise RuntimeError('expected 20+ test modules')
    names=names[args.start:args.end]
    env={k:v for k,v in os.environ.items()
         if not (k.startswith('BRAIN_') or k in ('LLM_API_KEY','OPENAI_API_KEY','GITHUB_TOKEN'))}
    records=[];alllog=[]
    for i,name in enumerate(names,1):
        start=time.monotonic()
        try:
            p=subprocess.run([sys.executable,'-u','-c',WORKER,name],cwd=from_repo,
                             capture_output=True,text=True,env=env,timeout=18)
            out=p.stdout+'\n'+p.stderr
            marker=re.search(r'OFFLINE_QA_MARKER (\d+) (\d+) (\d+)',out)
            count=int(marker.group(1)) if marker else 0
            ok=p.returncode==0 and bool(marker) and int(marker.group(2))==int(marker.group(3))==0
            record={'module':name,'status':'PASS' if ok else 'FAIL','tests':count,
                    'exit_code':p.returncode,'seconds':round(time.monotonic()-start,2)}
        except subprocess.TimeoutExpired as exc:
            record={'module':name,'status':'TIMEOUT','tests':0,'exit_code':None,
                    'seconds':round(time.monotonic()-start,2)}
            out=str(exc)
        all_log= '\n'+('='*20)+' '+name+' '+('='*20)+'\n'+out
        alllog.append(all_log)
        records.append(record)
        print(f"{i}/{len(names)} {name} {record['status']} {record['tests']} tests",flush=True)
    log=''.join(alllog)
    timestamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    receipt=folder/('v08-modules-'+timestamp+'.json')
    log_file=receipt.with_suffix('.log')
    log_file.write_text(log,encoding='utf-8')
    try:
        from scripts.release import verify_sources
        files=verify_sources(root)
    except BaseException as err:
        files=None
        print('MANIFEST_FAIL',type(err).__name__,str(err)[:200],flush=True)
    passed=all(x['status']=='PASS' for x in records) and files is not None and sum(x['tests'] for x in records)>0
    data={'status':'PASS' if passed else 'FAIL','source':from_repo,
          'mode':'per-module independent subprocess, network denied',
          'total_modules':len(records),'total_tests':sum(x['tests'] for x in records),
          'source_manifest_verified':files,'modules':records,
          'log_path':str(log_file),'log_sha256':hashlib.sha256(log.encode()).hexdigest(),
          'real_LLM_calls':0,'real_BRAIN_simulations':0,'real_BRAIN_submits':0,
          'genuine_LLM_discovery_evidence':False}
    receipt.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('COMPLETE',data['status'],data['total_tests'],'report',receipt,flush=True)
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
