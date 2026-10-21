"""Seeded moving-block bootstrap and frozen neighborhoods, never final holdout."""
from __future__ import annotations

import math
import random
import statistics

from .analysis_protocol import bind_protocol
from .common import digest
from .pnl_evidence import fixed_window_diagnostics
from .return_contract import EvidenceError, aligned_series, check, number, require_verified


def sharpe(values, annualization=1):
    if len(values) < 2 or not all(number(v) for v in values):
        return None
    sd = statistics.stdev(values)
    result = statistics.mean(values) / sd * math.sqrt(annualization) if sd > 1e-12 else None
    return result if result is not None and math.isfinite(result) else None


def quantile(ordered, q):
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def moving_block_sample(values, block_length, rng):
    """Non-circular overlapping blocks. Order is retained inside every block."""
    check(type(block_length) is int and 2 <= block_length <= len(values), 'invalid_moving_block_length')
    sample = []
    while len(sample) < len(values):
        start = rng.randrange(len(values) - block_length + 1)
        sample.extend(values[start:start + block_length])
    return sample[:len(values)]


def bootstrap_sharpe(series, *, block_length, replicates, seed, confidence, annualization):
    require_verified(series)
    check(type(replicates) is int and 100 <= replicates <= 3000 and type(seed) is int
          and number(confidence) and .5 <= confidence <= .99
          and type(annualization) is int and 1 <= annualization <= 366, 'invalid_bootstrap_options')
    check(type(block_length) is int and 2 <= block_length <= 126, 'invalid_moving_block_length')
    base = {'method': 'moving_block_percentile_v1', 'block_length': block_length,
            'seed': seed, 'replicates': replicates, 'confidence': confidence,
            'observed_days': len(series.returns), 'sharpe': None, 'confidence_interval': None,
            'annualization': annualization, 'risk_free_return_per_day': 0,
            'independent_observations_estimated': False, 'reasons': []}
    if len(series.returns) < max(126, 4 * block_length):
        return {**base, 'status': 'unavailable', 'reasons': ['insufficient_block_history']}
    if len(series.returns) * replicates > 8_000_000:
        return {**base, 'status': 'unavailable', 'reasons': ['offline_work_limit_exceeded']}
    point = sharpe(series.returns, annualization)
    if point is None:
        return {**base, 'status': 'unavailable', 'reasons': ['undefined_sharpe']}
    rng = random.Random(seed)
    scores = [sharpe(moving_block_sample(series.returns, block_length, rng), annualization)
              for _ in range(replicates)]
    # Do not selectively discard constant/undefined bootstrap replicates.
    if any(s is None for s in scores):
        return {**base, 'status': 'unavailable', 'reasons': ['undefined_bootstrap_replicate']}
    scores.sort()
    tail = (1 - confidence) / 2
    return {**base, 'status': 'computed', 'sharpe': point,
            'confidence_interval': [quantile(scores, tail), quantile(scores, 1 - tail)],
            'sample_sharpes_sha256': digest(scores),
            'notice': '依赖区块平稳性假设的历史诊断；置信区间不是未来业绩保证。'}


def robustness_report(series, protocol):
    require_verified(series)
    bind_protocol(protocol, [series])
    settings = protocol['bootstrap']
    windows = fixed_window_diagnostics(list(series.dates), list(series.returns), {
        'stability_window_sessions': protocol['stability_window_sessions'],
        'min_stability_windows': protocol['min_stability_windows'],
        'stability_annualization': protocol['annualization']})
    blocks = [bootstrap_sharpe(series, block_length=b, replicates=settings['replicates'],
              seed=settings['seed'], confidence=settings['confidence'], annualization=protocol['annualization'])
              for b in settings['block_lengths']]
    return {'candidate_id': series.candidate_id, 'input_sha256': series.input_sha256,
            'protocol_sha256': protocol['protocol_sha256'], 'synthetic': series.synthetic,
            'status': 'computed' if all(b['status'] == 'computed' for b in blocks) else 'unavailable',
            'bootstrap': blocks, 'fixed_windows': windows,
            'all_prespecified_blocks_reported': True, 'not_brain_metrics': True,
            'submission_eligibility_changed': False}


def plateau_report(series, protocol):
    """All prespecified neighbors, never a post-hoc best-only neighborhood."""
    bind_protocol(protocol, series)
    found = {s.candidate_id: s for s in series}
    reports = []
    for group in protocol.get('plateaus', []):
        base = {'family': group['family'], 'dimension': group['dimension'],
                'candidate_ids': group['candidate_ids'], 'parameter_values': group['parameter_values'],
                'baseline_id': group['baseline_id'], 'median_sharpe': None, 'worst_sharpe': None,
                'stable_fraction': None, 'cost_tradeoff': None}
        if any(i not in found for i in group['candidate_ids']):
            reports.append({**base, 'status': 'insufficient_evidence', 'reasons': ['missing_prespecified_neighbor']})
            continue
        members = [found[i] for i in group['candidate_ids']]
        try:
            aligned_series(members)
        except EvidenceError as error:
            reports.append({**base, 'status': 'insufficient_evidence', 'reasons': [str(error)]})
            continue
        scores = [sharpe(s.returns, protocol['annualization']) for s in members]
        if any(s is None for s in scores):
            reports.append({**base, 'status': 'unavailable', 'reasons': ['undefined_neighbor_sharpe']})
            continue
        baseline = scores[group['candidate_ids'].index(group['baseline_id'])]
        threshold = max(group['minimum_sharpe'], baseline * group['minimum_fraction_of_baseline'])
        reports.append({**base, 'status': 'computed', 'scores': dict(zip(group['candidate_ids'], scores)),
                        'median_sharpe': statistics.median(scores), 'worst_sharpe': min(scores),
                        'stable_fraction': sum(s >= threshold for s in scores) / len(scores),
                        'threshold': threshold, 'cost_tradeoff_reason': 'see_separate_cost_contract_report',
                        'reasons': [], 'best_candidate_selected': False})
    return reports
