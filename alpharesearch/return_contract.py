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


def submission_return_errors(result, *, alpha_id=None, account_ref=None):
    """Recompute stored evidence and bind it to this exact live Alpha.

    A verified label alone is never a submission credential. The evaluator and
    final write gate both call this, independently of the ranking decision.
    """
    if not isinstance(result, dict):
        return ['invalid_return_result']
    review = result.get('return_verification')
    if not isinstance(review, dict) or review.get('status') != 'verified':
        return ['unverified_return_contract']
    try:
        from .pnl_evidence import daily_evidence_valid
        check(daily_evidence_valid(result), 'unverified_or_incomplete_daily_pnl')
        series = verify_return_document(result.get('verified_return_document'))
        c = series.contract
        source = result.get('evidence_source')
        proof = result.get('daily_evidence')
        check(isinstance(source, dict) and isinstance(proof, dict), 'missing_return_binding')
        check(result.get('mode') == 'live' and result.get('synthetic', False) is False
              and series.synthetic is False and c['source_type'] == 'brain_pnl', 'nonlive_return_evidence')
        expected = alpha_id if alpha_id is not None else result.get('alpha_id')
        check(text_field(expected) and result.get('alpha_id') == expected
              and series.candidate_id == expected and c['source_id'] == expected, 'return_alpha_binding_conflict')
        check(source.get('transport') == 'account_http' and sha(source.get('account_ref'))
              and c['account_ref'] == source['account_ref']
              and (account_ref is None or account_ref == c['account_ref']), 'return_account_binding_conflict')
        check(isinstance(result.get('settings'), dict) and isinstance(result.get('expression'), str)
              and c['settings_sha256'] == digest(result['settings'])
              and c['expression_sha256'] == digest(result['expression']), 'return_formula_settings_conflict')
        check(c['scope'] == {k: result['settings'].get(k) for k in ('region', 'universe', 'delay')},
              'return_scope_conflict')
        check(c['is_interval'] == {'start': proof.get('start_date'), 'end': proof.get('end_date')},
              'return_interval_conflict')
        daily = result.get('daily_returns')
        sessions = proof.get('session_dates', [])
        keys = [a+'/'+b for a, b in zip(sessions, sessions[1:])]
        check(isinstance(daily, dict) and sorted(daily) == keys and len(keys) == len(series.returns)
              and all(number(daily[d]) and math.isclose(daily[d], v, rel_tol=1e-12, abs_tol=1e-15)
                      for d, v in zip(keys, series.returns)), 'return_daily_values_conflict')
        summary = series.summary()
        check(all(review.get(k) == v for k, v in summary.items()), 'return_review_fingerprint_conflict')
        document = result['verified_return_document']
        check(document['calendar'].get('source_file_sha256') == proof.get('calendar_sha256')
              and document['calendar']['sessions'] == proof.get('session_dates'), 'return_calendar_binding_conflict')
        responses = source.get('responses', {})
        check(c['review_source_sha256'] == responses.get('pnl', {}).get('payload_sha256')
              and c['capital_basis'].get('source_sha256') == responses.get('alpha', {}).get('payload_sha256'),
              'return_source_binding_conflict')
        return []
    except (EvidenceError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return ['invalid_or_unbound_return_contract']
