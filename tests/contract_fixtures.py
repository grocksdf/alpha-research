"""Independent, entirely synthetic live-result specification fixtures.

No production PnL adapter, evidence builder or summary reviewer constructs these
records. HTTP provenance is a test claim, never an actual account observation.
"""
import copy
import hashlib
import math
from datetime import date, timedelta

from alpharesearch.common import digest


def refresh_summary(result):
    doc = result['verified_return_document']
    c = doc['contract']
    normalized = {**c, 'calendar_id': doc['calendar']['id'], 'calendar_sha256': doc['calendar']['sha256']}
    result['return_verification'] = {
        'candidate_id': doc['candidate_id'], 'status': 'verified',
        'input_sha256': digest({k: doc[k] for k in ('contract', 'calendar', 'observations', 'candidate_id', 'synthetic')}),
        'observations': len(doc['observations'])-1,
        'start_date': doc['observations'][1]['date'], 'end_date': doc['observations'][-1]['date'],
        'return_unit': 'fraction', 'synthetic': False, 'contract_sha256': digest(normalized), 'empirical_evidence': True}


def independent_result(config):
    config = copy.deepcopy(config)
    days = [(date(2020, 1, 2)+timedelta(days=i)).isoformat() for i in range(505)]
    raw_calendar = 'date\n'+'\n'.join(days)+'\n'
    calendar_sha = hashlib.sha256(raw_calendar.encode()).hexdigest()
    pnl_review = {'reviewed': True, 'review_note': 'TEST ONLY independent fixture; no account data',
        'value_semantics': 'cumulative_cash_pnl_constant_capital_session_close',
        'pnl_unit': 'USD', 'capital_unit': 'USD', 'capital_path': ['is', 'bookSize'],
        'calendar_reviewed': True, 'calendar_id': 'TEST-INDEPENDENT', 'calendar_source': 'TEST daily synthetic sessions',
        'calendar_review_note': 'TEST ONLY includes nonmarket dates', 'calendar_region': 'USA',
        'calendar_start': days[0], 'calendar_end': days[-1], 'calendar_file': 'TEST_ONLY.csv', 'calendar_sha256': calendar_sha,
        'costs_reviewed': True, 'net_of_costs': False, 'cost_model_id': 'TEST-GROSS', 'cost_review_note': 'TEST ONLY gross fixture'}
    discovery_review = {'schema_reviewed': True, 'review_note': 'TEST ONLY IS boundaries',
        'date_semantics': 'inclusive_dates', 'is_start_path': ['is', 'startDate'], 'is_end_path': ['is', 'endDate']}
    config.update(mode='live', account_profile_verified=True)
    config['catalog_data'].update(source='brain-account-api', account_id='TEST_ONLY')
    config['brain']['pnl_evidence'] = pnl_review
    config['brain']['discovery_evidence'] = discovery_review
    config['safety'].update(live_research_enabled=True, submission_enabled=True)
    aid = 'INDEPENDENT_ALPHA'
    base = config['brain']['base_url']
    alpha_path = config['brain']['alpha'].format(id=aid)
    pnl_path = config['brain']['pnl'].format(id=aid)
    total = 1000.
    rows = [{'date': days[0], 'value': total}]
    for i, day in enumerate(days[1:]):
        total += .2 + math.sin(i*.71)
        rows.append({'date': day, 'value': total})
    keys = [a+'/'+b for a, b in zip(days, days[1:])]
    changes = {k: b['value']-a['value'] for k, a, b in zip(keys, rows, rows[1:])}
    returns = {k: value/100. for k, value in changes.items()}
    isolation = {'status': 'isolated', 'protocol': 'server-is-date-bounds-v1', 'metadata_source': 'alpha_detail.is',
        'start_date': days[0], 'end_date': days[-1], 'contract_sha256': digest(discovery_review),
        'start_path': ['is', 'startDate'], 'end_path': ['is', 'endDate']}
    proof = {'protocol': 'brain-session-pnl-v1', 'status': 'complete', 'value_semantics': pnl_review['value_semantics'],
        'contract_sha256': digest(pnl_review), 'pnl_unit': 'USD', 'capital_unit': 'USD',
        'constant_capital': 100., 'capital_path': ['is', 'bookSize'], 'start_date': days[0], 'end_date': days[-1],
        'session_dates': days, 'observed_dates': days, 'missing_dates': [], 'gap_intervals': [], 'off_calendar_dates': [],
        'expected_intervals': 504, 'accepted_intervals': 504, 'interval_coverage': 1.,
        **{k: pnl_review[k] for k in ('calendar_id', 'calendar_sha256', 'calendar_region', 'calendar_source',
            'calendar_start', 'calendar_end', 'review_note', 'calendar_review_note',
            'costs_reviewed', 'net_of_costs', 'cost_model_id', 'cost_review_note')}}
    result = {'mode': 'live', 'synthetic': False, 'alpha_id': aid, 'expression': 'ts_mean(close,20)',
        'settings': config['scope'], 'metrics': {'sharpe': 1.8, 'fitness': 1.4, 'turnover': .2},
        'checks': [{'name': k, 'result': 'PASS'} for k in sorted(set(config['policy']['required_checks']) | {'SELF_CORRELATION'})],
        'self_correlation': .2, 'production_correlation': .2, 'pnl_changes': changes,
        'daily_returns': returns, 'daily_evidence': proof, 'pnl_isolation': isolation,
        'years': [], 'evidence_errors': {}, 'collected_at': '2026-10-20T00:00:00+00:00'}
    alpha_payload = {'id': aid, 'settings': result['settings'], 'regular': {'code': result['expression']},
        'is': {'bookSize': 100., 'startDate': days[0], 'endDate': days[-1], **result['metrics']}}
    pnl_payload = {'records': [{'date': r['date'], 'pnl': r['value']} for r in rows]}
    source = {'transport': 'account_http', 'api_base_url': base, 'alpha_id': aid, 'account_ref': digest('TEST_ONLY'),
        'detail_path': alpha_path, 'pnl_path': pnl_path, 'discovery_contract_sha256': digest(discovery_review),
        'pnl_contract_sha256': digest(pnl_review), 'calendar_sha256': calendar_sha,
        'detail_reconciliation_sha256': digest({'id': aid, 'expression': result['expression'], 'settings': result['settings'], 'metrics': result['metrics']}),
        'normalized_is_pnl_sha256': digest({'pnl': changes, 'returns': returns, 'isolation': isolation}),
        'responses': {'alpha': {'method': 'GET', 'path': alpha_path, 'payload_sha256': digest(alpha_payload), 'observed_at': result['collected_at']},
                      'pnl': {'method': 'GET', 'path': pnl_path, 'payload_sha256': digest(pnl_payload), 'observed_at': result['collected_at']}}}
    contract = {'protocol': 'verified-return-contract-v1', 'sample_role': 'research_is', 'source_type': 'brain_pnl',
        'source_id': aid, 'population_id': digest('TEST_ONLY'), 'account_ref': digest('TEST_ONLY'),
        'scope': {k: config['scope'][k] for k in ('region', 'universe', 'delay')},
        'settings_sha256': digest(result['settings']), 'expression_sha256': digest(result['expression']),
        'api_base_url': base, 'alpha_path': alpha_path, 'pnl_path': pnl_path,
        'pnl_contract_sha256': digest(pnl_review), 'discovery_contract_sha256': digest(discovery_review),
        'reviewed': True, 'review_note': pnl_review['review_note'], 'review_source_sha256': digest(pnl_payload),
        'value_semantics': 'cumulative_cash_pnl', 'unit': 'USD', 'return_basis': 'constant_capital',
        'capital_basis': {'kind': 'constant', 'unit': 'USD', 'value': 100., 'reviewed': True,
            'source_path': ['is', 'bookSize'], 'source_sha256': digest(alpha_payload)},
        'no_resets_or_flows_reviewed': True,
        **{k: pnl_review[k] for k in ('costs_reviewed', 'net_of_costs', 'cost_model_id', 'cost_review_note')},
        'is_interval': {'start': days[0], 'end': days[-1]}, 'is_reviewed': True, 'excluded_intervals': []}
    calendar = {'id': proof['calendar_id'], 'source': proof['calendar_source'], 'region': 'USA',
        'sessions': days, 'sha256': digest(days), 'reviewed': True, 'review_note': proof['calendar_review_note'],
        'coverage_start': days[0], 'coverage_end': days[-1], 'source_file_sha256': calendar_sha}
    result['evidence_source'] = source
    result['verified_return_document'] = {'candidate_id': aid, 'contract': contract, 'calendar': calendar,
        'observations': rows, 'data_sha256': digest(rows), 'synthetic': False}
    refresh_summary(result)
    return config, result
