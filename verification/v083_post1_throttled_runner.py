#!/usr/bin/env python3
"""Run the genuine v0.8.3.post1 three-batch LLM acceptance with throttling.

This is a RUN-LAYER orchestration wrapper (not a source change): it reuses the
existing `make_plan` / `execute_batch` / `audit_real_rounds` API unchanged, but
inserts a cooldown between batches to respect the free-model provider's global
rate-limit window (`free_campaign_global_limit`). Every batch is still a real
`adapter=None` HTTP tool call; no script is ever injected and no retry of an
uncertain LLM POST is attempted. The safety gates remain identical to
`verification/v08_genuine_llm_acceptance.py`.

Safety invariants (unchanged):
  * mode=demo, live_research_enabled=false, submission_enabled=false
  * fresh dedicated SQLite database + frozen plan
  * no fixed Alpha seeds / scripted fallback
  * never Live BRAIN / Submit
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alpharesearch.common import ResearchError, load_settings
from alpharesearch.autoresearch_v08 import make_plan, execute_batch
from alpharesearch.llm_provider_policy import validate_llm_endpoint, _model_set
from alpharesearch.v08_pricing import reviewed_model_price
from verification.v08_genuine_llm_acceptance import audit_real_rounds


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--objective", required=True)
    p.add_argument("--batches", type=int, default=3)
    p.add_argument("--max-simulations-per-batch", type=int, default=2)
    p.add_argument("--max-llm-calls-per-batch", type=int, default=8)
    p.add_argument("--max-worker-repairs-per-candidate", type=int, default=2)
    p.add_argument("--max-critic-defer-rounds", type=int, default=2)
    p.add_argument("--max-empty-batches", type=int, default=2)
    p.add_argument("--max-tokens-per-batch", type=int, default=80000)
    p.add_argument("--max-known-usd-per-batch", type=float, required=True)
    p.add_argument("--max-wall-seconds", type=int, default=1800)
    p.add_argument("--cooldown-seconds", type=int, default=90,
                   help="cooldown between batches to respect free-model rate limit")
    p.add_argument("--allow-remote-llm", action="store_true")
    args = p.parse_args(argv)

    report = Path(args.report).resolve()
    try:
        config = load_settings(args.config)
        target = Path(args.database).resolve()
        original = Path(config["database"]).resolve()
        if target.exists() or target == original or target.name == original.name \
                or target.suffix not in (".db", ".sqlite3"):
            raise ResearchError("真实 LLM 验收必须使用不存在的新研究数据库")
        if (config["mode"] != "demo" or config["safety"].get("live_research_enabled")
                or config["safety"].get("submission_enabled")):
            raise ResearchError("真实 LLM 验收禁止所有真实 BRAIN 与 Submit 权限")
        if not 3 <= args.batches <= 10:
            raise ResearchError("真实 LLM 验收至少三轮、最多十轮")
        if not 0 < args.max_known_usd_per_batch <= 100:
            raise ResearchError("必须设置每批有限、正值的 USD 保护")
        if not os.getenv("LLM_API_KEY"):
            raise ResearchError("没有当前已授权 LLM_API_KEY；停止真实 LLM 验收，不模拟真实模型")
        if args.cooldown_seconds < 0:
            raise ResearchError("冷却时间不能为负")

        prices = [reviewed_model_price(config, model)
                  for model in _model_set(config["llm"])]
        policy = validate_llm_endpoint(config["llm"], operator_confirmed=args.allow_remote_llm)
        config["database"] = str(target)
        config["_v08_remote_operator_confirmed"] = bool(args.allow_remote_llm)
        plan = make_plan(config, project_id=args.project_id, objective=args.objective,
                         max_simulations_per_batch=args.max_simulations_per_batch,
                         max_llm_calls_per_batch=args.max_llm_calls_per_batch,
                         max_worker_repairs_per_candidate=args.max_worker_repairs_per_candidate,
                         max_critic_defer_rounds=args.max_critic_defer_rounds,
                         max_empty_batches=args.max_empty_batches,
                         max_wall_seconds=args.max_wall_seconds,
                         max_tokens_per_batch=args.max_tokens_per_batch,
                         max_known_usd_per_batch=args.max_known_usd_per_batch)

        outcomes = []
        throttled = False
        for i in range(args.batches):
            # adapter=None = genuine HTTP tool call, never a script injection.
            outcome = execute_batch(config, plan, adapter=None)
            outcomes.append(outcome)
            print(json.dumps({"batch": i + 1, "status": outcome.get("status"),
                              "new_trials": outcome.get("new_trials"),
                              "pending_revisions": outcome.get("pending_critic_revisions")},
                             ensure_ascii=False), flush=True)
            if i < args.batches - 1 and args.cooldown_seconds > 0:
                throttled = True
                time.sleep(args.cooldown_seconds)

        tested = audit_real_rounds(target, args.project_id, outcomes,
                                   min_rounds=args.batches)
        tested.update({
            "protocol": "v08-genuine-model-demo-acceptance-v1",
            "run_layer": "v083_post1_throttled_runner",
            "inter_batch_cooldown_seconds": args.cooldown_seconds,
            "cooldown_applied": throttled,
            "provider_policy_sha256": policy["policy_sha256"],
            "reviewed_price_receipts": [{
                "model": price["model"], "status": price["status"],
                "source": price["source"], "reviewed_at": price["reviewed_at"],
                "policy_sha256": price["price_policy_sha256"]}
                for price in prices],
            "project_plan_sha256": plan["plan_sha256"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "scripted_model_injected": False,
            "actual_provider_requests_intended": True,
            "independent_market_validation": False,
        })
    except Exception as err:
        tested = {"protocol": "v08-genuine-model-demo-acceptance-v1",
                  "status": "BLOCKED_OR_FAILED", "error_type": type(err).__name__,
                  "reason": str(err)[:400], "real_brain_simulations": 0,
                  "real_brain_submits": 0, "independent_market_validation": False}

    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf-8") as out:
        out.write(json.dumps(tested, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": tested["status"], "report": str(report),
                      "brain_simulations": 0, "submissions": 0}, ensure_ascii=False))
    return 0 if tested["status"] == "PASS_REAL_LLM_ENGINEERING_PROTOCOL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
