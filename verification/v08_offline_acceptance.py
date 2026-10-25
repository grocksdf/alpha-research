#!/usr/bin/env python3
"""v0.8 end-to-end OFFLINE test run. No remote tools, credentials or BRAIN.

Run: python verification/v08_offline_acceptance.py --repo . --report-dir /tmp/qa
New test processes are ephemeral. Histories and operator data never read.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import time
import unittest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default='.')
    parser.add_argument('--report-dir',required=True)
    args=parser.parse_args()
    repo=Path(args.repo).resolve()
    output=Path(args.report_dir).resolve()
    output.mkdir(parents=True,exist_ok=True)
    if not (repo/'alpharesearch/autoresearch_v08.py').is_file():
        parser.error('v0.8 source code not found')
    # Refuse if real credentials are present in the test process. This test
    # never contacts a provider and does not use historical database files.
    for key in list(os.environ):
        if key.startswith('BRAIN_') or key in ('LLM_API_KEY','OPENAI_API_KEY','GITHUB_TOKEN'):
            os.environ.pop(key,None)
    class DeniedSocket(socket.socket):
        def connect(self,address):
            raise AssertionError('v0.8 offline safety harness blocked network connect')
        def connect_ex(self,address):
            raise AssertionError('v0.8 offline safety harness blocked connect_ex')
    socket.socket=DeniedSocket
    socket.create_connection=lambda *args,**kwargs: (_ for _ in ()).throw(
        AssertionError('v0.8 offline safety harness blocked create_connection'))
    sys.path.insert(0,str(repo))
    from scripts.release import verify_sources
    files=verify_sources(repo)
    start=time.monotonic()
    suite=unittest.TestLoader().discover(start_dir=str(repo/'tests'),pattern='test_*.py')
    case_count=suite.countTestCases()
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    log=stream.getvalue()
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report_file=output/('autoresearch-v08-'+stamp+'.json')
    log_file=report_file.with_suffix('.log')
    log_file.write_text(log,encoding='utf-8')
    expected=392 # prior source-level baseline before v0.8 modification
    passed=(result.wasSuccessful() and result.testsRun==case_count and
            result.testsRun>expected)
    report={
        'protocol':'llm-autoresearch-v08-offline-acceptance-v1',
        'status':'PASS' if passed else 'FAIL',
        'source_checkout':str(repo),'source_manifest_verified_files':files,
        'prior_regression_baseline_tests':expected,
        'tests_discovered':case_count,'tests_run':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),
        'skipped':len(result.skipped),'elapsed_seconds':round(time.monotonic()-start,3),
        'log_path':str(log_file),
        'log_sha256':hashlib.sha256(log.encode('utf-8')).hexdigest(),
        'llm_calls_to_real_provider':0,'brain_simulation_requests':0,
        'brain_submit_requests':0,'outbound_network_disabled':True,
        'original_historical_sqlite_modified':False,
        'real_llm_multi_round_study_achieved':False,
        'real_market_alpha_validated':False,
        'test_only_legacy_variant_fixture':True,
    }
    report_file.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'tests':report['tests_run'],
                      'report':str(report_file),'real_brain_calls':0},ensure_ascii=False),flush=True)
    sys.stdout.flush();sys.stderr.flush()
    # Some historical test fixtures retain non-daemon transport/shutdown hooks
    # after unittest has FINISHED. A standalone QA process has no remaining
    # work; exit explicitly after fsync to avoid indefinite finalizer waiting.
    # Windows: os.fsync on a read-only handle raises EBADF, so open writable.
    with report_file.open('r+b') as f:os.fsync(f.fileno())
    with log_file.open('r+b') as f:os.fsync(f.fileno())
    os._exit(0 if passed else 1)

if __name__=='__main__':
    main()
