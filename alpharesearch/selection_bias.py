"""CSCV PBO and DSR diagnostics, conditional on a complete audited trial history.

Bailey et al. (2017), DOI 10.21314/JCF.2016.322; Bailey & Lopez de Prado
(2014), DOI 10.3905/jpm.2014.40.5.094. These are not submission gates.
"""
from __future__ import annotations

import itertools
import math
import statistics

from .analysis_protocol import bind_protocol
from .common import digest
from .return_contract import (EvidenceError, aligned_series, check, number,
                              require_verified, sha, text_field)
from .robustness import sharpe

ATTEMPT_STATES = frozenset(('queued', 'dispatching', 'polling', 'uncertain', 'paused',
                            'failed', 'rejected', 'completed', 'abandoned'))


def audited_history(history):
    check(isinstance(history, dict) and history.get('reviewed') is True
          and history.get('complete') is True and history.get('includes_failed_invalid_unfinished') is True
          and text_field(history.get('review_note')) and sha(history.get('source_sha256')),
          'complete_historical_attempts_not_audited')
    attempts = history.get('attempts')
    check(isinstance(attempts, list) and bool(attempts) and len(attempts) <= 100000,
          'missing_full_attempt_history')
    for row in attempts:
        check(isinstance(row, dict) and all(text_field(row.get(k)) for k in
              ('attempt_id', 'candidate_id', 'family')) and sha(row.get('variant_sha256'))
              and row.get('status') in ATTEMPT_STATES, 'invalid_historical_attempt')
    check(len({a['attempt_id'] for a in attempts}) == len(attempts)
          and type(history.get('total_attempts')) is int and history['total_attempts'] == len(attempts)
          and history.get('attempts_sha256') == digest(attempts), 'historical_attempt_count_or_hash_conflict')
    return attempts


def unavailable(method, reason, **extra):
    return {'method': method, 'status': 'unavailable', 'value': None, 'reasons': [reason], **extra}


def pbo(series, protocol, history):
    method = 'cscv_sharpe_v1'
    try:
        bind_protocol(protocol, series)
        aligned_series(series)
        settings = protocol['pbo']
        check(len(series) >= settings['min_candidates'], 'insufficient_candidates_minimum_eight')
        check({s.candidate_id for s in series} == set(protocol['candidate_universe']), 'missing_frozen_candidate_returns')
        attempts = audited_history(history)
        check({a['candidate_id'] for a in attempts} == set(protocol['candidate_universe']),
              'historical_candidate_universe_incomplete')
        check(all(a['status'] == 'completed' for a in attempts), 'historical_failed_or_unfinished_candidates_lack_comparable_returns')
        count = len(series[0].returns)
        check(count >= settings['min_observations'], 'insufficient_aligned_observations')
        blocks = settings['blocks']
        check(count // blocks >= 21, 'insufficient_observations_per_cscv_block')
        splits = math.comb(blocks, blocks // 2)
        check(count * len(series) * splits <= 12_000_000, 'cscv_offline_work_limit_exceeded')
        # Balanced contiguous time blocks; no row shuffle and no missing-date intersection.
        edges = [i * count // blocks for i in range(blocks + 1)]
        folds = []
        for train in itertools.combinations(range(blocks), blocks // 2):
            test = [i for i in range(blocks) if i not in train]
            def score(s, chosen):
                values = [v for b in chosen for v in s.returns[edges[b]:edges[b + 1]]]
                return sharpe(values)
            ins, outs = [score(s, train) for s in series], [score(s, test) for s in series]
            check(all(s is not None for s in ins + outs), 'undefined_cscv_fold_sharpe')
            best = max(ins)
            winners = [i for i, s in enumerate(ins) if math.isclose(s, best, abs_tol=1e-12, rel_tol=1e-12)]
            check(len(winners) == 1, 'cscv_training_tie_requires_new_preregistered_protocol')
            winner = winners[0]
            test_value = outs[winner]
            check(sum(math.isclose(s, test_value, abs_tol=1e-12, rel_tol=1e-12) for s in outs) == 1,
                  'cscv_test_rank_tie_requires_new_preregistered_protocol')
            rank = 1 + sum(s < test_value for s in outs)  # 1=worst, N=best.
            omega = rank / (len(series) + 1)
            logit = math.log(omega / (1 - omega))
            folds.append({'train_blocks': list(train), 'test_blocks': test,
                          'winner': series[winner].candidate_id, 'train_sharpe_daily': best,
                          'test_sharpe_daily': test_value, 'test_rank_ascending': rank,
                          'relative_rank': omega, 'logit': logit})
        return {'method': method, 'status': 'computed', 'value': sum(f['logit'] < 0 for f in folds) / len(folds),
                'candidates': len(series), 'observations': count, 'split_count': len(folds),
                'block_boundaries': [{'start': series[0].dates[edges[b]], 'end': series[0].dates[edges[b + 1] - 1]}
                                     for b in range(blocks)],
                'folds': folds, 'history_sha256': digest(attempts),
                'protocol_sha256': protocol['protocol_sha256'], 'reasons': [],
                'notice': '冻结候选集合内的条件性过拟合诊断，不是全部未来策略的过拟合概率。'}
    except EvidenceError as error:
        return unavailable(method, str(error), candidates=len(series))


def dsr(series, protocol, history):
    method = 'deflated_sharpe_daily_v1'
    try:
        require_verified(series)
        bind_protocol(protocol, [series])
        attempts = audited_history(history)
        check(series.candidate_id in {a['candidate_id'] for a in attempts}, 'selected_candidate_missing_from_history')
        check({a['candidate_id'] for a in attempts} == set(protocol['candidate_universe']),
              'historical_candidate_universe_incomplete')
        check(all(a['status'] in ('completed', 'rejected', 'failed') for a in attempts),
              'historical_trials_unfinished')
        calibration = history.get('dsr_calibration')
        check(isinstance(calibration, dict) and calibration.get('reviewed') is True
              and text_field(calibration.get('review_note')) and text_field(calibration.get('independence_method'))
              and sha(calibration.get('source_sha256')) and calibration.get('history_sha256') == digest(attempts),
              'independent_trial_count_and_full_history_variance_unverified')
        trials = calibration.get('effective_independent_trials')
        check(number(trials) and 8 <= trials <= len(attempts), 'insufficient_or_invalid_effective_independent_trials')
        variance = calibration.get('sharpe_variance_daily')
        check(number(variance) and variance > 0 and calibration.get('sharpe_unit') == 'daily_unannualized'
              and calibration.get('includes_all_attempts') is True
              and calibration.get('comparability_sha256') == series.comparability_key,
              'full_trial_sharpe_variance_unavailable')
        observations = calibration.get('effective_observations')
        check(isinstance(observations, dict), 'effective_sample_size_not_reviewed')
        sample = observations.get(series.candidate_id)
        check(isinstance(sample, dict) and sample.get('reviewed') is True and text_field(sample.get('method'))
              and sample.get('return_input_sha256') == series.input_sha256
              and number(sample.get('value')) and 126 <= sample['value'] <= len(series.returns),
              'effective_sample_size_not_reviewed')
        values = series.returns
        score = sharpe(values)
        check(score is not None, 'undefined_daily_sharpe')
        mean = statistics.mean(values)
        centered = [v - mean for v in values]
        m2 = statistics.mean(v * v for v in centered)
        check(m2 > 0, 'undefined_return_moments')
        skew = statistics.mean((v / math.sqrt(m2))**3 for v in centered)
        kurt = statistics.mean((v / math.sqrt(m2))**4 for v in centered)  # Pearson, not excess.
        normal = statistics.NormalDist()
        gamma = 0.5772156649015329
        threshold = math.sqrt(variance) * ((1 - gamma) * normal.inv_cdf(1 - 1 / trials)
                                         + gamma * normal.inv_cdf(1 - 1 / (trials * math.e)))
        denominator = 1 - skew * score + (kurt - 1) / 4 * score**2
        check(number(denominator) and denominator > 0, 'invalid_non_normal_sharpe_variance')
        z = (score - threshold) * math.sqrt(sample['value'] - 1) / math.sqrt(denominator)
        return {'method': method, 'status': 'computed', 'value': normal.cdf(z),
                'daily_sharpe': score, 'daily_null_max_sharpe': threshold,
                'skewness': skew, 'pearson_kurtosis': kurt, 'moment_estimator': 'population_central_moments',
                'observed_days': len(values), 'effective_observations': sample['value'],
                'total_attempts': len(attempts), 'effective_independent_trials': trials,
                'history_sha256': digest(attempts), 'protocol_sha256': protocol['protocol_sha256'],
                'reasons': [], 'notice': 'DSR 是条件性显著性诊断，不是有效 Alpha 的后验概率或未来收益保证。'}
    except EvidenceError as error:
        return unavailable(method, str(error))
