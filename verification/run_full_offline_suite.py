#!/usr/bin/env python3
"""Fail-closed full AlphaResearch regression runner with a signed-offline receipt.

Must run INSIDE an installed complete repository (after apply_cleanup --apply).
Runs existing security/release tests and new provenance tests; no credentials,
network sockets, BRAIN or LLM operations. Does not touch historical state DB.
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
import tempfile
import time
import unittest


def run_suite(root):
    # All configured test DBs from original suite are temporary. The standalone
    # runner never opens config/state.db or historic project state.
    class OfflineSocket(socket.socket):
        def connect(self, address):
            raise AssertionError('offline QA blocked a network connection')
        def connect_ex(self, address):
            raise AssertionError('offline QA blocked a network connection')
    socket.socket = OfflineSocket
    def forbidden_connection(*a, **kw):
        raise AssertionError('offline QA blocked outbound network access')
    socket.create_connection = forbidden_connection
    for name in list(os.environ):
        if (name.startswith('BRAIN_') or name in
                ('LLM_API_KEY', 'OPENAI_API_KEY', 'GITHUB_TOKEN')):
            os.environ.pop(name, None)
    from scripts.release import verify_sources
    verified_count = verify_sources(root)
    loader = unittest.TestLoader()
    # The upstream tests/ directory is not an importable package; use it as
    # the discovery top-level, matching `python -m unittest discover -s tests`.
    suite = loader.discover(start_dir=str(root/'tests'), pattern='test_*.py')
    count = suite.countTestCases()
    if count < 329:
        raise AssertionError('historical and new tests unexpectedly below baseline 329')
    stream = io.StringIO()
    start = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    elapsed = time.monotonic()-start
    body = stream.getvalue()
    return {
        'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
        'tests_discovered': count, 'errors': len(result.errors),
        'failures': len(result.failures), 'skipped': len(result.skipped),
        'elapsed_seconds': round(elapsed, 3),
        'source_files_verified': verified_count,
        'log': body,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--report-dir', default=None)
    args=parser.parse_args(argv)
    root=Path(args.repo).resolve()
    if not (root/'alpharesearch/discovery.py').is_file():
        parser.error('Run only inside complete AlphaResearch checkout')
    sys.path.insert(0, str(root))
    report_folder=(Path(args.report_dir).resolve() if args.report_dir else
                   root/'state'/'offline-qa-reports')
    report_folder.mkdir(parents=True,exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report=report_folder/('live-template-isolation-'+stamp+'.json')
    log=report.with_suffix('.log')
    outcome={'protocol':'live-llm-discovery-isolation-qa-v1',
             'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
             'overall':'FAIL', 'full_upstream_tests':'NOT_RUN',
             'real_llm_requests':0,'real_brain_simulations':0,'real_submits':0,
             'unmodified_historical_runs':True,
             'live_alpha_performance_proven':False}
    try:
        measured=run_suite(root)
        text=measured.pop('log')
        with log.open('x',encoding='utf-8') as out:
            out.write(text)
            out.flush();os.fsync(out.fileno())
        outcome['full_upstream_tests']=('PASS' if measured['passed'] else 'FAIL')
        outcome['suite']=measured
        outcome['test_log_sha256']=hashlib.sha256(text.encode('utf-8')).hexdigest()
        outcome['test_log_file']=str(log)
        outcome['overall']='PASS' if measured['passed'] else 'FAIL'
    except BaseException as error:
        outcome['blocker_type']=type(error).__name__
        outcome['blocker_description']=str(error)[:500]
    with report.open('x',encoding='utf-8') as out:
        out.write(json.dumps(outcome,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        out.flush();os.fsync(out.fileno())
    print(json.dumps({'status':outcome['overall'],'report':str(report),
        'tests':outcome.get('suite',{}).get('tests_run'),
        'live_llm_requests':0,'brain_simulations':0,'submits':0},ensure_ascii=False))
    return 0 if outcome['overall']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
