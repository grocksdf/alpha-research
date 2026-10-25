"""Offline, bounded one-dimensional experiment design. No new executor or POST."""
from __future__ import annotations

import re

from alpharesearch.common import digest, now, snapshot
from alpharesearch.preview import preview
from alpharesearch.return_contract import check, sha, text_field
from alpharesearch.validator import Validator

PROTOCOL = 'bounded-evolution-plan-v1'


def create_plan(config, spec):
    check(isinstance(spec, dict) and text_field(spec.get('study_id')), 'missing_evolution_study_id')
    check(spec.get('sample_role') == 'research_is', 'evolution_holdout_input_forbidden')
    maximum = spec.get('max_new_simulations', 0)
    check(type(maximum) is int and 0 <= maximum <= min(4, config['rules']['max_simulations_per_run']),
          'evolution_planned_budget_must_be_zero_to_four')
    families = spec.get('families')
    check(isinstance(families, list) and 1 <= len(families) <= 4, 'invalid_evolution_families')
    validator = Validator(config['catalog_data'], config['scope'], config['rules'])
    candidates = []
    family_ids = set()
    for group in families:
        check(isinstance(group, dict) and all(text_field(group.get(k)) for k in
              ('family_id', 'dimension', 'template', 'expected_direction', 'counter_hypothesis')),
              'missing_single_parameter_hypothesis')
        name, template = group['dimension'], group['template']
        check(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) is not None
              and template.count('{') == template.count('}') == 1
              and '{' + name + '}' in template, 'exactly_one_parameter_placeholder_required')
        check(group['family_id'] not in family_ids, 'duplicate_evolution_family')
        family_ids.add(group['family_id'])
        values = group.get('values')
        check(isinstance(values, list) and 2 <= len(values) <= 5
              and all(type(v) is int and 2 <= v <= 252 for v in values)
              and values == sorted(set(values)) and type(group.get('baseline_value')) is int
              and group['baseline_value'] in values, 'invalid_prespecified_parameter_range')
        baseline = group['baseline_value']
        baseline_expression = template.format(**{name: baseline})
        for value in values:
            expression = template.format(**{name: value})
            valid = validator.validate(expression)
            candidates.append({'key': group['family_id'] + ':' + str(value), 'family': group['family_id'],
                'dimension': name, 'parameter': value, 'baseline_parameter': baseline,
                'baseline_expression': baseline_expression, 'expression': expression,
                'fingerprint': valid.fingerprint, 'change': {} if value == baseline else
                {name: {'before': baseline, 'after': value}},
                'hypothesis': group['expected_direction'], 'counter_hypothesis': group['counter_hypothesis'],
                'source': PROTOCOL})
    check(len(candidates) <= config['rules']['max_proposals_per_run'], 'candidate_design_exceeds_proposal_budget')
    independent = spec.get('independent_validation')
    check(isinstance(independent, dict) and independent.get('feedback_to_agent') is False
          and independent.get('mode') == 'separate_frozen_validation'
          and (independent.get('manifest_sha256') is None or sha(independent['manifest_sha256'])),
          'independent_validation_binding_required')
    result = {'protocol': PROTOCOL, 'study_id': spec['study_id'], 'registered_at': now(),
              'snapshot_sha256': digest(snapshot(config)), 'scope': config['scope'],
              'spec': spec, 'candidates': candidates, 'max_simulations': maximum,
              'execution_enabled': False, 'new_simulations_authorized': 0,
              'independent_validation': independent, 'automatic_sensitivity': False}
    result['plan_sha256'] = digest(result)
    return result


def validate_plan(config, plan):
    check(isinstance(plan, dict) and plan.get('protocol') == PROTOCOL
          and plan.get('plan_sha256') == digest({k: v for k, v in plan.items() if k != 'plan_sha256'}),
          'evolution_plan_hash_mismatch')
    check(plan.get('snapshot_sha256') == digest(snapshot(config)) and plan.get('scope') == config['scope'],
          'evolution_plan_snapshot_changed')
    # Rebuild semantics from the frozen specification; a rehashed edited plan is
    # not allowed to turn on an executor or silently add a second dimension.
    expected = create_plan(config, plan.get('spec'))
    check(all(plan.get(k) == v for k, v in expected.items() if k not in ('registered_at', 'plan_sha256')),
          'evolution_plan_semantics_changed')
    return plan


def preview_plan(config, plan):
    validate_plan(config, plan)
    estimate = preview(config, plan['candidates'], plan=plan, lookup_study=False)
    estimate.update(operation='evolution-preview', execution_enabled=False,
                    planned_new_simulation_ceiling=plan['max_simulations'],
                    planned_new_simulations_upper_bound=estimate['new_simulations_upper_bound'],
                    new_simulations_upper_bound=0, new_simulations_authorized=0,
                    independent_validation_bound=sha(plan['independent_validation'].get('manifest_sha256')),
                    notice='本版只冻结和预览；不提供 evolution-run，不授权新增模拟。')
    estimate.pop('preview_sha256', None)
    estimate['preview_sha256'] = digest(estimate)
    return estimate
