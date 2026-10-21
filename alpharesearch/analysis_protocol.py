"""Frozen offline analysis inputs. Hashes record changes, not registration timing."""
from __future__ import annotations

import json
from pathlib import Path

from .common import ResearchError, digest, now
from .return_contract import check, exact_day, number, text_field, MANIFEST

PROTOCOL = 'robust-research-analysis-v1'


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ResearchError('JSON 重复字段：' + key)
            result[key] = value
        return result
    def finite_float(value):
        parsed=float(value)
        if not number(parsed):raise ResearchError('JSON 含非有限数值')
        return parsed
    try:
        path = Path(path)
        if path.stat().st_size > 60_000_000:
            raise ResearchError('分析 JSON 超过 60 MB 限制')
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,parse_float=finite_float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ResearchError('JSON 含非有限数值')))
    except (OSError, ValueError, UnicodeError) as error:
        raise ResearchError('无法读取分析 JSON') from error


def validate_protocol(value):
    check(isinstance(value, dict) and value.get('protocol') == PROTOCOL, 'unknown_analysis_protocol')
    check(value.get('protocol_sha256') == digest({k: v for k, v in value.items() if k != 'protocol_sha256'}),
          'analysis_protocol_hash_mismatch')
    check(text_field(value.get('study_id')) and value.get('sample_role') == 'research_is'
          and value.get('frozen_before_observation') is True and text_field(value.get('registration_note')),
          'research_preregistration_required')
    ids = value.get('candidate_universe')
    check(isinstance(ids, list) and 1 <= len(ids) <= 64 and all(text_field(i) for i in ids)
          and len(ids) == len(set(ids)), 'invalid_frozen_candidate_universe')
    bounds = value.get('is_interval')
    check(isinstance(bounds, dict) and exact_day(bounds.get('start')) < exact_day(bounds.get('end')),
          'invalid_frozen_is_interval')
    check(type(value.get('annualization')) is int and 1 <= value['annualization'] <= 366,
          'invalid_annualization')
    check(number(value.get('risk_free_return_per_day')) and value['risk_free_return_per_day']==0,
          'this_protocol_requires_explicit_zero_risk_free_benchmark')
    bootstrap = value.get('bootstrap')
    check(isinstance(bootstrap, dict), 'missing_bootstrap_protocol')
    blocks = bootstrap.get('block_lengths')
    check(isinstance(blocks, list) and 1 <= len(blocks) <= 5 and all(type(b) is int and 2 <= b <= 126 for b in blocks)
          and blocks == sorted(set(blocks)), 'invalid_frozen_block_lengths')
    check(type(bootstrap.get('replicates')) is int and 100 <= bootstrap['replicates'] <= 3000
          and type(bootstrap.get('seed')) is int and 0 <= bootstrap['seed'] < 2**63
          and number(bootstrap.get('confidence')) and .5 <= bootstrap['confidence'] <= .99,
          'invalid_bootstrap_protocol')
    check(type(value.get('stability_window_sessions')) is int and 2 <= value['stability_window_sessions'] <= 1000
          and type(value.get('min_stability_windows')) is int and value['min_stability_windows'] >= 2,
          'invalid_fixed_stability_protocol')
    pbo = value.get('pbo')
    check(isinstance(pbo, dict) and type(pbo.get('blocks')) is int and 8 <= pbo['blocks'] <= 12
          and pbo['blocks'] % 2 == 0 and pbo.get('metric') == 'daily_sharpe'
          and pbo.get('ties') == 'unavailable', 'invalid_cscv_protocol')
    check(type(pbo.get('min_candidates')) is int and 8 <= pbo['min_candidates'] <= 64
          and type(pbo.get('min_observations')) is int and pbo['min_observations'] >= 252,
          'pbo_minimum_cannot_be_relaxed_for_four_alphas')
    plateau = value.get('plateaus', [])
    check(isinstance(plateau, list), 'invalid_plateau_protocol')
    for group in plateau:
        check(isinstance(group, dict) and text_field(group.get('family')) and text_field(group.get('dimension')),
              'missing_plateau_family_or_dimension')
        members = group.get('candidate_ids')
        parameters = group.get('parameter_values')
        check(isinstance(members, list) and len(members) >= 3 and all(text_field(i) for i in members)
              and len(set(members)) == len(members)
              and set(members) <= set(ids) and group.get('baseline_id') in members
              and isinstance(parameters, list) and len(parameters) == len(members)
              and all(number(x) for x in parameters) and len(set(parameters)) == len(parameters),
              'invalid_frozen_parameter_neighborhood')
        check(number(group.get('minimum_sharpe')) and number(group.get('minimum_fraction_of_baseline'))
              and 0 <= group['minimum_fraction_of_baseline'] <= 1, 'invalid_plateau_threshold')
    costs = value.get('cost_scenarios', [])
    check(isinstance(costs, list) and len(costs) <= 10, 'invalid_cost_scenarios')
    for scenario in costs:
        check(isinstance(scenario, dict) and text_field(scenario.get('id'))
              and scenario.get('kind') == 'incremental_cost' and number(scenario.get('bps'))
              and 0 <= scenario['bps'] <= 1000, 'cost_scenarios_must_be_explicit_incremental_assumptions')
    check(len({c['id'] for c in costs}) == len(costs), 'duplicate_cost_scenario')
    return value


def freeze_protocol(spec):
    check(isinstance(spec, dict) and 'protocol_sha256' not in spec, 'provide_unfrozen_analysis_spec')
    value = {**spec, 'protocol': PROTOCOL, 'registered_at': now()}
    value['protocol_sha256'] = digest(value)
    return validate_protocol(value)


def load_manifest(path):
    value = read_json(path)
    check(isinstance(value, dict) and value.get('format') == MANIFEST, 'unknown_returns_manifest')
    docs = value.get('series')
    check(isinstance(docs, list) and 1 <= len(docs) <= 64, 'invalid_returns_manifest_series')
    ids = [d.get('candidate_id') if isinstance(d, dict) else None for d in docs]
    check(all(text_field(i) for i in ids) and len(set(ids)) == len(ids), 'invalid_or_duplicate_manifest_candidate')
    return value


def bind_protocol(protocol, series):
    validate_protocol(protocol)
    for s in series:
        check(s.candidate_id in protocol['candidate_universe']
              and s.contract['is_interval'] == protocol['is_interval'], 'series_outside_frozen_protocol')
