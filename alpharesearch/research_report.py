"""Offline, non-promoting research diagnostics with reproducible input identities."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .analysis_protocol import bind_protocol, validate_protocol
from .checks import summarize_checks
from . import __version__
from .common import digest
from .cost_stress import cost_stress
from .return_contract import EvidenceError, MANIFEST, check, review_return_document
from .robustness import plateau_report, robustness_report
from .selection_bias import dsr, pbo


def analyze_manifest(manifest, protocol):
    validate_protocol(protocol)
    check(isinstance(manifest, dict) and manifest.get('format') == MANIFEST
          and isinstance(manifest.get('series'), list) and 1 <= len(manifest['series']) <= 64,
          'invalid_returns_manifest')
    verified, items = [], []
    ids = [d.get('candidate_id') if isinstance(d, dict) else None for d in manifest['series']]
    check(all(isinstance(i, str) and i for i in ids) and len(ids) == len(set(ids))
          and set(ids) <= set(protocol['candidate_universe']), 'manifest_candidates_outside_frozen_universe')
    turnovers = manifest.get('turnover_contracts', {})
    check(isinstance(turnovers, dict), 'invalid_turnover_contract_map')
    for document in manifest['series']:
        series, evidence = review_return_document(document)
        item = {'candidate_id': document['candidate_id'], 'evidence': evidence,
                'robustness': None, 'dsr': None, 'cost_stress': None,
                'candidate_status': 'evidence_incomplete', 'ready_to_submit': False}
        if series is not None:
            try:
                bind_protocol(protocol, [series])
                verified.append(series)
                item.update(robustness=robustness_report(series, protocol),
                            return_contract=series.contract,
                            dsr=dsr(series, protocol, manifest.get('history')),
                            cost_stress=cost_stress(series, protocol, turnovers.get(series.candidate_id)),
                            candidate_status='research_candidate')
            except EvidenceError as error:
                item['evidence'] = {'status': 'insufficient_evidence', 'reasons': [str(error)]}
        items.append(item)
    missing = sorted(set(protocol['candidate_universe']) - set(ids))
    for candidate in missing:
        items.append({'candidate_id': candidate, 'evidence': {'status': 'insufficient_evidence',
                      'reasons': ['missing_prespecified_candidate']}, 'robustness': None, 'dsr': None,
                      'cost_stress': None, 'candidate_status': 'evidence_incomplete', 'ready_to_submit': False})
    selection = pbo(verified, protocol, manifest.get('history'))
    parameters = plateau_report(verified, protocol)
    costs_by_id={item['candidate_id']:item['cost_stress'] for item in items}
    for group in parameters:
        costs=[costs_by_id.get(i) for i in group['candidate_ids']]
        if not costs or any(not c or c.get('status')!='computed' for c in costs):continue
        baseline=costs[group['candidate_ids'].index(group['baseline_id'])]
        comparisons=[]
        for index,scenario in enumerate(baseline['scenarios']):
            if any(c['scenarios'][index]['net_return_metrics'] is None for c in costs):continue
            base_mean=scenario['net_return_metrics']['mean_daily_return']
            comparisons.append({'scenario_id':scenario['id'],'hypothetical':True,
                'mean_daily_return_difference_from_baseline':{i:c['scenarios'][index]['net_return_metrics']['mean_daily_return']-base_mean
                    for i,c in zip(group['candidate_ids'],costs)}})
        if comparisons:group['cost_tradeoff']={'scenarios':comparisons,'not_brain_reported_metrics':True}
    modules=('__init__.py','common.py','return_contract.py','analysis_protocol.py','pnl_evidence.py',
             'robustness.py','selection_bias.py','cost_stress.py','research_report.py','checks.py')
    source=Path(__file__).parent
    code_sha=digest({name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in modules})
    identity = digest({'manifest': manifest, 'protocol': protocol, 'analysis_code_sha256':code_sha})
    return {'version': __version__, 'mode': 'offline_research_diagnostics', 'study_id': protocol['study_id'],
            'analysis_id': identity, 'analysis_code_sha256':code_sha, 'algorithm_version':'robust-research-v1',
            'manifest_sha256': digest(manifest), 'protocol_sha256': protocol['protocol_sha256'],
            'status': 'computed' if len(verified) == len(protocol['candidate_universe']) else 'insufficient_evidence',
            'series': items, 'pbo': selection, 'parameter_plateaus': parameters,
            'synthetic': any(s.synthetic for s in verified), 'real_return_contracts_verified': bool(verified)
                and all(not s.synthetic for s in verified) and len(verified) == len(protocol['candidate_universe']),
            'network_requests': 0, 'simulation_posts': 0, 'submit_posts': 0,
            'submission_enabled': False, 'database_mutated': False, 'final_holdout_accessed': False,
            'feedback_to_agent': False, 'repeated_input_is_new_independent_evidence': False,
            'notice': '离线诊断不更改固定评价器或提交资格；本地审核与冻结时间不构成平台认证。'}


def research_report(manifest, protocol, review=None):
    result = analyze_manifest(manifest, protocol)
    result['mode'] = 'offline_research_report'
    result['run_id'] = None
    result['trials'] = []
    result['costs'] = None
    result['budget'] = None
    result['snapshot_sha256'] = None
    result['source_review_verified'] = False
    result['report_id']=result['analysis_id']
    # This invocation cannot send any submit POST. Historical counts are unknown
    # unless supplied by an independent network audit, never inferred from status.
    result['historical_submit_posts'] = None
    if review is None:
        result['source_review_reason'] = 'no_engine_review_supplied'
        return result
    check(isinstance(review, dict), 'invalid_engine_review')
    expected_hash = digest({k: v for k, v in review.items() if k not in ('created_at', 'audit', 'review_hash')})
    check(review.get('review_hash') == expected_hash, 'engine_review_hash_mismatch')
    snapshot = review.get('snapshot', {})
    check(isinstance(snapshot, dict) and review.get('snapshot_hash')==digest(snapshot)
          and isinstance(snapshot.get('policy',{}),dict) and isinstance(snapshot.get('scope',{}),dict), 'invalid_review_snapshot')
    required=snapshot.get('policy',{}).get('required_checks',[])
    check(isinstance(required,list) and all(isinstance(k,str) for k in required), 'invalid_review_check_policy')
    result.update(run_id=review.get('run_id'), snapshot_sha256=review.get('snapshot_hash'),
                  costs=review.get('costs'), budget=review.get('counts'), source_review_verified=True)
    result['report_id']=digest({'analysis_id':result['analysis_id'],'source_review_hash':review['review_hash']})
    summaries = {s['candidate_id']: s for s in result['series']}
    candidates = review.get('candidates', [])
    check(isinstance(candidates, list) and isinstance(review.get('trials', []), list), 'invalid_review_candidates')
    for c in candidates:
        if not isinstance(c, dict):
            continue
        item = summaries.get(c.get('alpha_id'))
        if item is None:
            continue
        evaluation = c.get('evaluation', {})
        if not isinstance(evaluation, dict):
            evaluation = {}
        checks = summarize_checks(c.get('checks'), required)
        contract=item.get('return_contract',{})
        source=c.get('evidence_source')
        source=source if isinstance(source,dict) else {}
        reconciled=(contract.get('source_type')=='brain_pnl'
                    and contract.get('account_ref')==source.get('account_ref')
                    and contract.get('expression_sha256')==digest(c.get('expression'))
                    and contract.get('settings_sha256')==digest(c.get('settings'))
                    and contract.get('scope')=={k:snapshot.get('scope',{}).get(k) for k in ('region','universe','delay')})
        ready = bool(review.get('mode') == 'live' and not c.get('synthetic')
                     and item['evidence'].get('empirical_evidence') is True and reconciled
                     and item['evidence']['status'] == 'verified' and evaluation.get('ready') is True
                     and checks['submission_checks_complete'])
        # Diagnostics never turn a failed/incomplete evaluator result into a pass.
        state = ('ready_for_human_review' if ready else 'rejected' if evaluation.get('rejections')
                 else 'evidence_incomplete' if (not evaluation.get('ready') or not checks['submission_checks_complete']
                    or not reconciled or not item['evidence'].get('empirical_evidence')) else 'research_candidate')
        item.update(candidate_status=state, ready_to_submit=False,
                    trial_id=c.get('trial_id'), expression=c.get('expression'), family=c.get('family'),
                    source=c.get('source'), settings=c.get('settings'), brain_metrics=c.get('metrics'),
                    brain_checks=c.get('checks'), check_state=checks, evaluator_decision=evaluation,
                    return_identity_reconciled=reconciled,
                    evidence_source=c.get('evidence_source'))
    # Keep failed, invalid and unfinished trials in the report, without pretending
    # this one run proves completeness of the account's lifetime search history.
    result['trials'] = review.get('trials', [])
    result['multiplicity_history_complete_from_single_run'] = False
    return result
