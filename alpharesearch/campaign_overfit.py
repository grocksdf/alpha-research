"""Fail-closed diagnostics on explicit VERIFIED IS return series only.

These diagnostics are research controls, NOT a final independent holdout
attestation. The final protected validation ledger is intentionally never read
by this module or included in agent feedback.
"""
from __future__ import annotations

import math
from .common import ResearchError, digest
from .return_contract import require_verified

def _pearson(a, b):
    if len(a) < 3 or len(a) != len(b):
        return None
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    va = sum((x-ma)**2 for x in a)
    vb = sum((y-mb)**2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb)

def correlation_precheck(candidate, established, *, threshold=0.7, min_overlap=252):
    """Absolute correlation, including negative redundancy; no AST proxy."""
    if candidate is None:
        return {"status": "unavailable", "reason": "verified_is_returns_missing"}
    candidate = require_verified(candidate)
    if candidate.synthetic:
        return {"status": "unavailable", "reason": "synthetic_returns_not_market_evidence"}
    if not 0 < threshold < 1:
        raise ResearchError("invalid_correlation_threshold")
    max_abs, compared = 0.0, 0
    for other in established:
        other = require_verified(other)
        if other.synthetic or candidate.comparability_key != other.comparability_key:
            continue
        by_day = dict(zip(other.dates, other.returns))
        pair = [(x, by_day[d]) for d,x in zip(candidate.dates, candidate.returns) if d in by_day]
        if len(pair) < min_overlap:
            continue
        corr = _pearson([x for x,_ in pair], [y for _,y in pair])
        if corr is None:
            continue
        max_abs = max(max_abs, abs(corr))
        compared += 1
    if compared == 0:
        return {"status": "unavailable", "reason": "no_comparable_verified_series"}
    return {"status": "reject" if max_abs > threshold else "pass",
            "max_absolute_correlation": max_abs, "compared": compared,
            "threshold": threshold, "evidence_type": "verified_research_is_only"}

def walk_forward_audit(series, *, train_size, test_size, step=None, min_folds=3):
    """Fixed predeclared chronological windows, no parameter search per fold.

    Unlike the final holdout, this uses research IS data and is diagnostic only.
    """
    series = require_verified(series)
    if series.synthetic:
        return {"status": "unavailable", "reason": "synthetic_returns_not_market_evidence"}
    for name, value in (("train_size",train_size), ("test_size",test_size),
                        ("min_folds",min_folds)):
        if type(value) is not int or value < 2:
            raise ResearchError("invalid_walk_forward_" + name)
    step = test_size if step is None else step
    if type(step) is not int or step < test_size:
        raise ResearchError("walk-forward 测试窗口不得重叠")
    obs = series.returns
    folds = []
    for stop in range(train_size, len(obs)-test_size+1, step):
        held = obs[stop:stop+test_size]
        avg = sum(held)/len(held)
        folds.append({"train_end": series.dates[stop-1],
                      "test_start": series.dates[stop], "test_end": series.dates[stop+test_size-1],
                      "test_mean_daily_return": avg,
                      "positive_mean": avg > 0})
    if len(folds) < min_folds:
        return {"status": "unavailable", "reason": "insufficient_predeclared_folds",
                "fold_count": len(folds)}
    return {"status": "diagnostic_only", "fold_count": len(folds),
            "positive_fold_fraction": sum(f["positive_mean"] for f in folds)/len(folds),
            "folds": folds,
            "protocol_sha256": digest({"train": train_size, "test": test_size,
                                      "step": step, "min_folds": min_folds,
                                      "series_sha256": series.input_sha256}),
            "independent_holdout_validated": False}
