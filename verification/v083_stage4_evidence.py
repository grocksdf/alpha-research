#!/usr/bin/env python3
"""Stage 4 independent-evidence collector for the v0.8.3.post1 genuine-LLM
three-batch acceptance.

Reads a completed campaign SQLite database and emits a single redacted,
machine-checkable JSON bundle covering the Stage 4 deliverables:
  * redacted call log (request/response provenance, no secrets, no raw text)
  * batch report (per-batch role counts, trial counts, status)
  * formula provenance (expression -> hypothesis -> fingerprint chain)
  * revision chain (parent-child hypothesis + worker revision depth)
  * knowledge review records (playbook knowledge + host review decisions)

Safety: this collector only reads. It redacts model names to their configured
identity and NEVER emits raw prompt/body text, API keys, or credentials. It
makes no claim about market returns or causal memory uplift.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _rows(db, sql, *args):
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(sql, args)]


def collect(dbpath: str, project_id: str) -> dict:
    db = sqlite3.connect(dbpath)
    db.row_factory = sqlite3.Row
    try:
        # ---- project / plan provenance ----
        plan_row = db.execute(
            "SELECT plan_json, plan_sha256, status FROM v08_projects WHERE project_id=?",
            (project_id,)).fetchone()
        plan = json.loads(plan_row["plan_json"]) if plan_row else {}
        plan_summary = {
            "project_id": project_id,
            "plan_sha256": plan_row["plan_sha256"] if plan_row else None,
            "project_status": plan_row["status"] if plan_row else None,
            "protocol": plan.get("protocol"),
            "objective": plan.get("objective"),
            "snapshot_sha256": plan.get("snapshot_sha256"),
            "generator": plan.get("generator"),
        }

        # ---- batches ----
        batches = _rows(db,
            "SELECT id, run_id, batch_number, status FROM v08_batches "
            "WHERE project_id=? ORDER BY batch_number", project_id)

        # ---- redacted call log ----
        calls = []
        for b in batches:
            for r in _rows(db,
                "SELECT r.role, r.model, r.state, r.id AS request_id, "
                "r.response_id, r.tool_call_id, r.prompt_sha256, r.reply_sha256, "
                "p.original_raw_sha256, p.canonical_body_sha256, p.created "
                "FROM v08_requests r LEFT JOIN v08_role_payloads p ON p.request_id=r.id "
                "WHERE r.run_id=? ORDER BY r.created, r.id", b["run_id"]):
                calls.append({
                    "batch": b["batch_number"],
                    "role": r["role"],
                    "model": r["model"],
                    "state": r["state"],
                    "request_id": r["request_id"],
                    "response_id": r["response_id"],
                    "tool_call_id": r["tool_call_id"],
                    "prompt_sha256": r["prompt_sha256"],
                    "reply_sha256": r["reply_sha256"],
                    "original_raw_sha256": r["original_raw_sha256"],
                    "canonical_body_sha256": r["canonical_body_sha256"],
                    "created": r["created"],
                })

        # ---- hypotheses + formula provenance + revision chain ----
        hypotheses = _rows(db,
            "SELECT id, parent_id, source_request_id, body_sha256, created "
            "FROM v08_hypotheses WHERE project_id=? ORDER BY created, id", project_id)
        queue = _rows(db,
            "SELECT id, hypothesis_id, expression, fingerprint, trial_id, state, reason "
            "FROM v08_queue WHERE project_id=? ORDER BY created, id", project_id)
        revisions = _rows(db,
            "SELECT id, hypothesis_id, expression_sha256, parent_revision_id, "
            "depth, state, defer_reason FROM v08_revisions "
            "WHERE project_id=? ORDER BY created, id", project_id)

        formula_provenance = []
        for q in queue:
            formula_provenance.append({
                "queue_id": q["id"],
                "hypothesis_id": q["hypothesis_id"],
                "expression": q["expression"],
                "fingerprint": q["fingerprint"],
                "trial_id": q["trial_id"],
                "state": q["state"],
                "reason": q["reason"],
            })

        revision_chain = []
        for h in hypotheses:
            if h["parent_id"]:
                revision_chain.append({
                    "hypothesis_id": h["id"],
                    "parent_hypothesis_id": h["parent_id"],
                    "source_request_id": h["source_request_id"],
                    "body_sha256": h["body_sha256"],
                })
        for rev in revisions:
            revision_chain.append({
                "revision_id": rev["id"],
                "hypothesis_id": rev["hypothesis_id"],
                "expression_sha256": rev["expression_sha256"],
                "parent_revision_id": rev["parent_revision_id"],
                "depth": rev["depth"],
                "state": rev["state"],
            })

        # ---- knowledge + reviews ----
        knowledge = _rows(db,
            "SELECT id, topic, evidence_ids, evidence_grade, status, supersedes_id, created "
            "FROM v08_knowledge WHERE project_id=? ORDER BY created, id", project_id)
        reviews = _rows(db,
            "SELECT request_id, proposal_index, item_sha256, decision, knowledge_id, reason "
            "FROM v08_knowledge_reviews WHERE project_id=? ORDER BY created", project_id)

        # ---- trial count ----
        trials = db.execute(
            "SELECT COUNT(*) FROM trials WHERE run_id IN "
            "(SELECT run_id FROM v08_batches WHERE project_id=?)", (project_id,)).fetchone()[0]

        # ---- cost summary ----
        cost_rows = _rows(db,
            "SELECT c.run_id, c.input_tokens, c.output_tokens, c.usd "
            "FROM costs c JOIN v08_batches b ON b.run_id=c.run_id "
            "WHERE b.project_id=? AND c.category='llm'", project_id)
        known_usd = sum(r["usd"] for r in cost_rows if r["usd"] is not None)
        known_tokens = sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0)
                           for r in cost_rows)

        return {
            "protocol": "v083-post1-stage4-evidence-v1",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "plan": plan_summary,
            "batches": [{"batch_number": b["batch_number"], "status": b["status"],
                         "run_id": b["run_id"]} for b in batches],
            "batch_count": len(batches),
            "redacted_call_log": calls,
            "hypotheses_count": len(hypotheses),
            "formula_provenance": formula_provenance,
            "revision_chain": revision_chain,
            "parent_child_hypothesis_revisions": sum(1 for h in hypotheses if h["parent_id"]),
            "knowledge": knowledge,
            "knowledge_reviews": reviews,
            "demo_trials": trials,
            "llm_cost": {
                "known_usd": known_usd,
                "known_tokens": known_tokens,
                "cost_rows": len(cost_rows),
            },
            "real_brain_simulations": 0,
            "real_brain_submits": 0,
            "independent_market_validation": False,
            "causal_memory_uplift_claimed": False,
        }
    finally:
        db.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--report", required=True)
    args = p.parse_args(argv)
    bundle = collect(args.database, args.project_id)
    out = Path(args.report).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({
        "status": "COLLECTED",
        "report": str(out),
        "batches": bundle["batch_count"],
        "hypotheses": bundle["hypotheses_count"],
        "demo_trials": bundle["demo_trials"],
        "known_usd": bundle["llm_cost"]["known_usd"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
