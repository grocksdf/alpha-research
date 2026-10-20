"""Fixed gates and a diverse Pareto shortlist. The Research Agent cannot configure this."""
from __future__ import annotations

import math
import statistics

from .brain import finite, pnl_isolation_valid
from .pnl_evidence import stability_windows, daily_evidence_valid


def aligned_correlation(left, right, min_overlap):
    keys = sorted(left.keys() & right.keys())
    if len(keys) < min_overlap:
        return None
    x, y = [left[k] for k in keys], [right[k] for k in keys]
    if not all(finite(v) is not None for v in x+y) or statistics.pvariance(x) == 0 or statistics.pvariance(y) == 0:
        return None
    return statistics.correlation(x, y)


def evaluate(result, neighbors, policy, account_verified=False):
    reject, missing = [], []
    metrics = result.get("metrics", {})
    for key in ("sharpe", "fitness", "turnover"):
        if finite(metrics.get(key)) is None:
            missing.append("missing_" + key)
    core_complete=not missing
    if finite(metrics.get('sharpe')) is not None and metrics["sharpe"] < policy["min_sharpe"]:
        reject.append("low_sharpe")
    if finite(metrics.get('fitness')) is not None and metrics["fitness"] < policy["min_fitness"]:
        reject.append("low_fitness")
    if finite(metrics.get('turnover')) is not None and not policy["min_turnover"] <= metrics["turnover"] <= policy["max_turnover"]:
        reject.append("turnover_outside_research_range")
    checks = result.get("checks", [])
    by_name = {x["name"]: x.get("result") for x in checks}
    for name in policy["required_checks"]:
        if name not in by_name:
            missing.append("missing_check:"+name)
    for check in checks:
        status, name = check.get("result"), check["name"]
        if status == "PASS":
            continue
        # Some account tiers only resolve this check after submit. Pre-submit endpoint is the gate.
        if name == "SELF_CORRELATION" and status == "PENDING" and finite(result.get("self_correlation")) is not None:
            continue
        if status in ("FAIL", "WARNING"):
            reject.append("platform_check:"+name+":"+status)
        else:
            missing.append("unresolved_check:"+name+":"+str(status))
    corr = finite(result.get("self_correlation"))
    if corr is None:
        missing.append("missing_self_correlation")
    elif abs(corr) >= policy["max_abs_self_correlation"]:
        reject.append("high_self_correlation")
    if policy["require_production_correlation"]:
        prod = finite(result.get("production_correlation"))
        if prod is None:
            missing.append("missing_production_correlation")
        elif abs(prod) >= policy["max_abs_production_correlation"]:
            reject.append("high_production_correlation")
    isolated=pnl_isolation_valid(result)
    live=result.get('mode')=='live' and not result.get('synthetic')
    if live and not isolated:missing.append('unverified_pnl_sample_isolation')
    reliable=daily_evidence_valid(result)
    if not reliable:missing.append('unverified_or_incomplete_daily_pnl')
    stability=stability_windows(result,policy)
    positive_fraction=stability['positive_fraction']
    if not stability['complete']:
        missing.extend(stability['reasons'])
    elif positive_fraction<policy['min_positive_window_fraction'] or stability['worst_window_sharpe']<policy['min_window_sharpe']:
        reject.append('unstable_fixed_windows')
    valid_neighbors = [n.get("metrics", {}) for n in neighbors if n]
    if len(valid_neighbors) < policy["min_sensitivity_neighbors"]:
        missing.append("insufficient_parameter_neighbors")
    elif finite(metrics.get("sharpe")) is not None:
        for n in valid_neighbors:
            if finite(n.get("sharpe")) is None or finite(n.get("fitness")) is None:
                missing.append("missing_neighbor_metrics")
            elif n["sharpe"] < metrics["sharpe"]*policy["min_neighbor_sharpe_ratio"] or n["fitness"] < policy["min_neighbor_fitness"]:
                reject.append("parameter_sensitive")
    changes = result.get("pnl_changes", {})
    if len(changes) < policy["min_pnl_overlap"] or any(finite(x) is None for x in changes.values()):
        missing.append("insufficient_pnl_for_diversity")
    if not account_verified:
        missing.append("account_profile_unverified")
    if result.get("mode") != "live" or result.get("synthetic"):
        missing.append("demo_not_live_evidence")
    ready = not reject and not missing
    research_eligible=core_complete and not reject
    return {"status": "ready_for_human_review" if ready else "rejected" if reject else "evidence_incomplete",
            "ready": ready, "rejections": sorted(set(reject)), "missing": sorted(set(missing)),
            'research_eligible':research_eligible,
            'research_status':'continue_research' if research_eligible and not ready else 'review_ready' if ready else 'rejected' if reject else 'needs_core_metrics',
            "positive_window_fraction": positive_fraction,
            "worst_window_sharpe": stability['worst_window_sharpe'], 'stability':stability,
            "independent_holdout_validated": False}


def shortlist(items, policy, family_cap=2):
    candidates = [x for x in items if x["evaluation"]["ready"]]
    # Pareto order uses fitness, worst fixed window, turnover, correlation. No single reward score.
    def vector(x):
        return (x["metrics"]["fitness"], x["evaluation"]["worst_window_sharpe"], -x["metrics"]["turnover"], -abs(x["self_correlation"]))
    def dominates(a, b):
        av, bv = vector(a), vector(b)
        return all(x >= y for x, y in zip(av, bv)) and any(x > y for x, y in zip(av, bv))
    # Do NOT read candidates while it is being sorted. CPython temporarily
    # empties a list during list.sort(), making the domination count zero.
    remaining = list(candidates)
    pareto_layer = {}
    layer = 0
    while remaining:
        front = [x for x in remaining if not any(
            dominates(y, x) for y in remaining if y["trial_id"] != x["trial_id"])]
        if not front:
            raise ValueError("Pareto front cannot be formed from candidate metrics")
        for x in front:
            pareto_layer[x["trial_id"]] = layer
        selected_ids = {x["trial_id"] for x in front}
        remaining = [x for x in remaining if x["trial_id"] not in selected_ids]
        layer += 1
    candidates=[{**x,'pareto_layer':pareto_layer[x['trial_id']]} for x in candidates]
    candidates.sort(key=lambda x: (pareto_layer[x["trial_id"]],
                                   -x["evaluation"]["worst_window_sharpe"], -x["metrics"]["fitness"], x["trial_id"]))
    selected, excluded, families = [], [], {}
    for candidate in candidates:
        reason = None
        if families.get(candidate["family"], 0) >= family_cap:
            reason = "family_cap"
        else:
            for chosen in selected:
                left=candidate.get('daily_evidence',{});right=chosen.get('daily_evidence',{})
                if left and right and any(left.get(k)!=right.get(k) for k in ('calendar_sha256','contract_sha256','value_semantics')):
                    reason='daily_evidence_protocol_mismatch'
                    break
                corr = aligned_correlation(candidate["pnl_changes"], chosen["pnl_changes"], policy["min_pnl_overlap"])
                if corr is None:
                    reason = "insufficient_pairwise_overlap"
                    break
                if abs(corr) >= policy["max_abs_pool_correlation"]:
                    reason = "pool_correlation:"+chosen["trial_id"]
                    break
        if reason:
            excluded.append({"trial_id": candidate["trial_id"], "reason": reason})
        else:
            selected.append(candidate)
            families[candidate["family"]] = families.get(candidate["family"], 0)+1
    return selected, excluded
