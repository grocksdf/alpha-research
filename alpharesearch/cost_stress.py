"""Incremental, explicitly hypothetical cost stress on verified daily returns."""
from __future__ import annotations

from .analysis_protocol import bind_protocol
from .common import digest
from .return_contract import EvidenceError, check, number, require_verified, sha, text_field
from .robustness import sharpe


def cost_stress(series, protocol, turnover_contract=None):
    require_verified(series)
    bind_protocol(protocol, [series])
    scenarios = protocol.get('cost_scenarios', [])
    base = {'candidate_id': series.candidate_id, 'method': 'incremental_cost_scenarios_v1',
            'base_net_of_costs': series.contract['net_of_costs'], 'base_cost_model_id': series.contract['cost_model_id'],
            'hypothetical_scenarios': True, 'not_brain_reported_metrics': True,
            'base_costs_subtracted_again': False, 'scenarios': []}
    try:
        c = turnover_contract
        check(isinstance(c, dict) and c.get('reviewed') is True and text_field(c.get('review_note'))
              and sha(c.get('source_sha256')), 'daily_turnover_contract_not_reviewed')
        check(c.get('candidate_id') == series.candidate_id and c.get('return_input_sha256') == series.input_sha256,
              'turnover_identity_or_return_hash_mismatch')
        check(c.get('unit') == 'one_way_traded_notional_over_same_capital'
              and c.get('cost_basis') == 'incremental_only', 'turnover_or_cost_basis_unknown')
        rows = c.get('observations')
        check(isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
              and [r.get('date') for r in rows] == list(series.dates)
              and all(number(r.get('value')) and r['value'] >= 0 for r in rows)
              and c.get('data_sha256') == digest(rows), 'daily_turnover_dates_or_values_invalid')
    except EvidenceError as error:
        return {**base, 'status': 'assumption_only', 'reasons': [str(error)],
                'scenarios': [{**s, 'net_return_metrics': None, 'reason': str(error)} for s in scenarios]}
    for scenario in scenarios:
        values = [r - t['value'] * scenario['bps'] / 10000 for r, t in zip(series.returns, rows)]
        valid = all(number(r) and r > -1 for r in values)
        score = sharpe(values, protocol['annualization']) if valid else None
        base['scenarios'].append({**scenario, 'status': 'computed' if score is not None else 'unavailable',
            'net_return_metrics': {'sharpe': score, 'mean_daily_return': sum(values) / len(values)} if score is not None else None,
            'return_sha256': digest(values) if valid else None,
            'interpretation': 'additional_cost_stress_on_existing_net' if series.contract['net_of_costs'] else 'modeled_net_under_assumed_incremental_cost',
            'reason': None if score is not None else 'invalid_or_undefined_stressed_return'})
    complete=all(s['status']=='computed' for s in base['scenarios'])
    return {**base, 'status': 'computed' if complete else 'unavailable', 'turnover_contract_sha256': digest(c),
            'reasons': [] if complete else ['unavailable_stressed_scenarios']}
