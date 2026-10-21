"""Explicit return units and research-sample provenance. No unit/calendar inference.

Reviewed local metadata prevents mistakes; it is not a platform attestation or a
security boundary against an operator who can rewrite files.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date

from .common import ResearchError, digest, dumps
from .http import ApiError
from .pnl_evidence import validate_sessions, validate_session_boundaries

PROTOCOL = 'verified-return-contract-v1'
MANIFEST = 'verified-returns-manifest-v1'
_SEAL = object()


class EvidenceError(ResearchError):
    pass


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def text_field(value):
    return isinstance(value, str) and bool(value.strip())


def check(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def exact_day(value):
    check(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is not None,
          'invalid_exact_date')
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise EvidenceError('invalid_exact_date') from error
    return value


@dataclass(frozen=True)
class VerifiedReturnSeries:
    candidate_id: str
    dates: tuple[str, ...]
    returns: tuple[float, ...]
    input_sha256: str
    synthetic: bool
    _contract_json: str = field(repr=False)
    _seal: object = field(repr=False, compare=False)

    @property
    def contract(self):
        return json.loads(self._contract_json)

    @property
    def comparability_key(self):
        c = self.contract
        # Different Alpha IDs may be compared, different source populations may not.
        return digest({k: c[k] for k in ('source_type', 'population_id', 'scope',
                      'net_of_costs', 'cost_model_id', 'return_basis', 'is_interval')})

    def summary(self):
        return {'candidate_id': self.candidate_id, 'status': 'verified',
                'input_sha256': self.input_sha256, 'observations': len(self.dates),
                'start_date': self.dates[0], 'end_date': self.dates[-1],
                'return_unit': 'fraction', 'synthetic': self.synthetic,
                'contract_sha256': digest(self.contract),
                'empirical_evidence': not self.synthetic}


def require_verified(series):
    check(isinstance(series, VerifiedReturnSeries) and series._seal is _SEAL,
          'verified_return_series_required')
    return series


def verify_return_document(document):
    check(isinstance(document, dict), 'invalid_return_document')
    c, calendar, rows = (document.get(k) for k in ('contract', 'calendar', 'observations'))
    check(isinstance(c, dict) and isinstance(calendar, dict), 'missing_return_contract')
    check(c.get('protocol') == PROTOCOL, 'unsupported_return_protocol')
    check(document.get('synthetic') in (True, False) and type(document.get('synthetic')) is bool,
          'synthetic_provenance_required')
    check(text_field(document.get('candidate_id')), 'missing_candidate_id')
    check(c.get('sample_role') == 'research_is', 'holdout_or_unknown_sample_forbidden')
    check(c.get('reviewed') is True and text_field(c.get('review_note'))
          and sha(c.get('review_source_sha256')), 'return_semantics_not_reviewed')
    check(c.get('source_type') in ('brain_pnl', 'independent_pit_panel'), 'unknown_source_type')
    check(text_field(c.get('source_id')) and text_field(c.get('population_id')), 'missing_source_identity')
    scope = c.get('scope')
    check(isinstance(scope, dict) and all(text_field(scope.get(k)) for k in ('region', 'universe'))
          and type(scope.get('delay')) is int and scope['delay'] >= 1, 'missing_source_scope')
    if c['source_type'] == 'brain_pnl':
        check(sha(c.get('account_ref')) and c['population_id'] == c['account_ref'], 'unverified_account_identity')
        check(c.get('source_id') == document['candidate_id'], 'alpha_identity_conflict')
        check(sha(c.get('settings_sha256')) and sha(c.get('expression_sha256')), 'missing_alpha_identity_hashes')
    else:
        check(c.get('pit_reviewed') is True and text_field(c.get('dataset_id'))
              and c['population_id'] == c['dataset_id'], 'point_in_time_dataset_unverified')
    check(type(c.get('net_of_costs')) is bool and c.get('costs_reviewed') is True
          and text_field(c.get('cost_model_id')) and text_field(c.get('cost_review_note')),
          'unknown_cost_semantics')
    check(c.get('return_basis') in ('constant_capital', 'total_return_nav', 'simple_return'),
          'unknown_return_basis')
    interval = c.get('is_interval')
    check(isinstance(interval, dict), 'missing_exact_is_interval')
    start, end = exact_day(interval.get('start')), exact_day(interval.get('end'))
    check(start < end and c.get('is_reviewed') is True, 'unverified_is_interval')
    excluded = c.get('excluded_intervals')
    check(isinstance(excluded, list), 'missing_excluded_sample_declaration')
    for other in excluded:
        check(isinstance(other, dict), 'invalid_excluded_interval')
        left, right = exact_day(other.get('start')), exact_day(other.get('end'))
        check(left <= right and (right < start or left > end), 'mixed_is_os_intervals')
    check(calendar.get('reviewed') is True and all(text_field(calendar.get(k)) for k in
          ('id', 'source', 'review_note')) and calendar.get('region') == scope['region'],
          'calendar_not_reviewed_for_market')
    sessions = calendar.get('sessions')
    try:
        sessions = validate_sessions(sessions)
        validate_session_boundaries(sessions, start, end)
    except (ApiError, TypeError, ValueError) as error:
        raise EvidenceError('invalid_or_truncated_calendar') from error
    check(calendar.get('sha256') == digest(sessions), 'calendar_hash_mismatch')
    check(exact_day(calendar.get('coverage_start')) <= start <= sessions[0]
          and sessions[-1] <= end <= exact_day(calendar.get('coverage_end')), 'calendar_coverage_conflict')
    check(isinstance(rows, list) and 2 <= len(rows) <= 30000, 'invalid_observation_count')
    check(all(isinstance(row, dict) and set(row) == {'date', 'value'} for row in rows), 'invalid_observation_row')
    days = [exact_day(row['date']) for row in rows]
    check(days == sorted(set(days)), 'duplicate_or_unsorted_dates')
    check(days == sessions, 'missing_or_off_calendar_or_mixed_sample_dates')
    values = [row['value'] for row in rows]
    check(all(number(v) for v in values), 'nonfinite_observation')
    check(document.get('data_sha256') == digest(rows), 'observation_hash_mismatch')
    semantics = c.get('value_semantics')
    unit, basis = c.get('unit'), c.get('capital_basis')
    check(isinstance(basis, dict), 'missing_capital_basis')
    if semantics in ('daily_cash_pnl', 'cumulative_cash_pnl'):
        check(isinstance(unit, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,15}', unit) is not None,
              'unknown_cash_unit')
        capital = basis.get('value')
        check(c['return_basis'] == 'constant_capital' and basis.get('kind') == 'constant'
              and basis.get('unit') == unit and number(capital) and capital > 0
              and basis.get('reviewed') is True and sha(basis.get('source_sha256')),
              'unverified_constant_capital')
        check(c.get('no_resets_or_flows_reviewed') is True, 'cash_pnl_resets_or_flows_unknown')
        if semantics == 'cumulative_cash_pnl':
            returns = [(b - a) / capital for a, b in zip(values, values[1:])]
            days = days[1:]
        else:
            returns = [v / capital for v in values]
    elif semantics == 'nav':
        check(c['return_basis'] == 'total_return_nav' and basis.get('kind') == 'total_return_nav'
              and basis.get('reviewed') is True and sha(basis.get('source_sha256'))
              and text_field(unit) and c.get('no_resets_or_flows_reviewed') is True,
              'nav_flow_or_distribution_semantics_unknown')
        check(all(v > 0 for v in values), 'nonpositive_nav')
        returns = [b / a - 1 for a, b in zip(values, values[1:])]
        days = days[1:]
    elif semantics == 'daily_return':
        check(unit == 'fraction' and c['return_basis'] == 'simple_return'
              and basis.get('kind') == 'already_normalized' and basis.get('reviewed') is True,
              'return_unit_or_normalization_unknown')
        returns = values
    else:
        raise EvidenceError('unknown_daily_or_cumulative_semantics')
    check(all(number(v) and -1 < v <= 1e100 for v in returns), 'invalid_normalized_simple_return')
    fingerprint = digest({'contract': c, 'calendar': calendar, 'observations': rows,
                          'candidate_id': document['candidate_id'], 'synthetic': document['synthetic']})
    # Bind calendar identity to comparisons without permitting caller mutation.
    c = {**c, 'calendar_id': calendar['id'], 'calendar_sha256': calendar['sha256']}
    return VerifiedReturnSeries(document['candidate_id'], tuple(days), tuple(float(v) for v in returns),
                                fingerprint, document['synthetic'], dumps(c), _SEAL)


def review_return_document(document):
    try:
        series = verify_return_document(document)
        return series, series.summary()
    except EvidenceError as error:
        upstream=document.get('verification') if isinstance(document,dict) else None
        reasons=[str(error)]
        if isinstance(upstream,dict) and isinstance(upstream.get('reasons'),list):
            reasons+= [r for r in upstream['reasons'] if isinstance(r,str) and r not in reasons]
        return None, {'candidate_id': document.get('candidate_id') if isinstance(document, dict) else None,
                      'status': 'insufficient_evidence', 'reasons': reasons,
                      'observations': None, 'performance_metrics_computed': False}


def aligned_series(series):
    check(isinstance(series, (list, tuple)) and bool(series), 'no_verified_series')
    for item in series:
        require_verified(item)
    check(len({s.candidate_id for s in series}) == len(series), 'duplicate_candidate_id')
    first = series[0]
    for other in series[1:]:
        check(other.dates == first.dates, 'candidate_dates_not_identical')
        check(other.comparability_key == first.comparability_key
              and other.contract['calendar_sha256'] == first.contract['calendar_sha256']
              and other.synthetic == first.synthetic, 'incompatible_return_populations_or_costs')
    return series


def require_result_return_contract(result, *, expected_alpha_id=None,
                                   expected_account_ref=None, expected_api_base_url=None):
    """Recheck a live result, not just its stored 'verified' summary.

    This pure guard is called separately by evaluation and final submission.
    It neither fetches evidence nor changes historical records or permissions.
    """
    from .pnl_evidence import daily_evidence_valid
    check(isinstance(result, dict), 'invalid_result_return_evidence')
    summary=result.get('return_verification')
    check(isinstance(summary, dict) and summary.get('status')=='verified'
          and not summary.get('reasons'), 'return_verification_not_verified')
    check(result.get('mode')=='live' and not result.get('synthetic'), 'live_return_evidence_required')
    document=result.get('verified_return_document')
    series=verify_return_document(document)
    check(not series.synthetic, 'synthetic_return_contract_forbidden')
    expected=series.summary()
    check(all(summary.get(k)==v and type(summary.get(k)) is type(v) for k,v in expected.items()),
          'return_verification_summary_mismatch')
    contract=series.contract
    alpha_id=result.get('alpha_id')
    check(text_field(alpha_id) and series.candidate_id==alpha_id
          and contract.get('source_type')=='brain_pnl' and contract.get('source_id')==alpha_id,
          'return_contract_alpha_identity_mismatch')
    if expected_alpha_id is not None:
        check(alpha_id==expected_alpha_id, 'return_contract_approved_alpha_mismatch')
    source=result.get('evidence_source')
    check(isinstance(source,dict) and source.get('transport')=='account_http'
          and source.get('alpha_id')==alpha_id and sha(source.get('account_ref'))
          and source['account_ref']==contract.get('account_ref'), 'return_contract_account_source_mismatch')
    if expected_account_ref is not None:
        check(sha(expected_account_ref) and source['account_ref']==expected_account_ref,
              'return_contract_current_account_mismatch')
    if expected_api_base_url is not None:
        check(source.get('api_base_url')==expected_api_base_url, 'return_contract_api_source_mismatch')
    settings=result.get('settings')
    check(isinstance(settings,dict) and contract.get('settings_sha256')==digest(settings)
          and contract.get('expression_sha256')==digest(result.get('expression'))
          and contract.get('scope')=={k:settings.get(k) for k in ('region','universe','delay')},
          'return_contract_expression_or_settings_mismatch')
    responses=source.get('responses')
    check(isinstance(responses,dict) and all(isinstance(responses.get(k),dict) for k in ('alpha','pnl')),
          'return_contract_response_provenance_missing')
    check(all(responses[k].get('method')=='GET' and sha(responses[k].get('payload_sha256')) for k in ('alpha','pnl'))
          and contract.get('review_source_sha256')==responses['pnl']['payload_sha256']
          and contract['capital_basis'].get('source_sha256')==responses['alpha']['payload_sha256'],
          'return_contract_response_hash_mismatch')
    check(daily_evidence_valid(result), 'return_contract_daily_evidence_incomplete')
    proof=result['daily_evidence'];isolation=result['pnl_isolation']
    check(contract.get('value_semantics')=='cumulative_cash_pnl' and contract.get('return_basis')=='constant_capital'
          and contract['capital_basis'].get('value')==proof['constant_capital']
          and contract.get('unit')==proof['pnl_unit'] and contract['capital_basis'].get('unit')==proof['capital_unit'],
          'return_contract_pnl_unit_or_capital_mismatch')
    check(contract.get('is_interval')=={'start':isolation['start_date'],'end':isolation['end_date']}
          and document['calendar']['sessions']==proof['session_dates']
          and document['calendar'].get('source_file_sha256')==proof['calendar_sha256']
          and series.dates==tuple(proof['session_dates'][1:]), 'return_contract_is_or_calendar_mismatch')
    keys=[a+'/'+b for a,b in zip(proof['session_dates'],proof['session_dates'][1:])]
    check(all(math.isclose(v,result['daily_returns'][k],rel_tol=1e-12,abs_tol=1e-15)
              for k,v in zip(keys,series.returns)), 'return_contract_daily_values_mismatch')
    return series


def brain_return_document(config, result, payload):
    """Adapt audited, isolated BRAIN observations. Unknown costs remain unknown."""
    from .brain import records
    from .pnl_evidence import daily_evidence_valid
    check(daily_evidence_valid(result), 'unverified_or_incomplete_daily_pnl')
    source, proof = result.get('evidence_source', {}), result['daily_evidence']
    check(result.get('mode') == 'live' and not result.get('synthetic')
          and source.get('transport') == 'account_http', 'real_account_transport_unverified')
    reviewed = config['brain'].get('pnl_evidence', {})
    settings=result.get('settings')
    check(isinstance(settings,dict), 'missing_alpha_settings')
    start, end = proof['start_date'], proof['end_date']
    rows = [{'date': row['date'], 'value': row.get('pnl')} for row in records(payload)
            if isinstance(row.get('date'), str) and start <= row['date'] <= end]
    rows.sort(key=lambda row: row['date'])
    contract = {'protocol': PROTOCOL, 'sample_role': 'research_is', 'source_type': 'brain_pnl',
        'source_id': result['alpha_id'], 'population_id': source.get('account_ref'),
        'account_ref': source.get('account_ref'), 'scope': {k: settings.get(k) for k in ('region', 'universe', 'delay')},
        'settings_sha256': digest(result.get('settings')), 'expression_sha256': digest(result.get('expression')),
        'reviewed': reviewed.get('reviewed'), 'review_note': reviewed.get('review_note'),
        'review_source_sha256': source.get('responses', {}).get('pnl', {}).get('payload_sha256'),
        'value_semantics': 'cumulative_cash_pnl', 'unit': proof['pnl_unit'],
        'capital_basis': {'kind': 'constant', 'unit': proof['capital_unit'], 'value': proof['constant_capital'],
                          'reviewed': True, 'source_sha256': source.get('responses', {}).get('alpha', {}).get('payload_sha256')},
        'return_basis': 'constant_capital', 'no_resets_or_flows_reviewed': True,
        'net_of_costs': reviewed.get('net_of_costs'), 'costs_reviewed': reviewed.get('costs_reviewed'),
        'cost_model_id': reviewed.get('cost_model_id'), 'cost_review_note': reviewed.get('cost_review_note'),
        'is_interval': {'start': start, 'end': end}, 'is_reviewed': True, 'excluded_intervals': []}
    calendar = {'id': proof['calendar_id'], 'source': proof['calendar_source'], 'region': proof['calendar_region'],
        'sessions': proof['session_dates'], 'sha256': digest(proof['session_dates']), 'reviewed': True,
        'review_note': reviewed.get('calendar_review_note'), 'coverage_start': proof['calendar_start'],
        'coverage_end': proof['calendar_end'], 'source_file_sha256': proof['calendar_sha256']}
    return {'candidate_id': result['alpha_id'], 'contract': contract, 'calendar': calendar,
            'observations': rows, 'data_sha256': digest(rows), 'synthetic': False}
