#!/usr/bin/env python3
"""Run a real (NOT scripted) 3+ round v0.8 LLM-only / Demo acceptance.

Requires an actual operator-approved, tools-capable model, LLM_API_KEY and a
NEW database. Never opens BRAIN Live or Submit, never injects candidate data.
This is an opt-in network tool; merely importing it does not contact providers.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

# Support direct `python verification/v08_genuine_llm_acceptance.py` from the
# complete checkout, without requiring an editable package installation.
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from alpharesearch.common import ResearchError, load_settings, digest
from alpharesearch.autoresearch_v08 import make_plan, execute_batch, Playbook, PHASES
from alpharesearch.llm_provider_policy import validate_llm_endpoint
from alpharesearch.memory import Memory


def audit_real_rounds(dbpath, project_id, responses, *, min_rounds=3):
    """Validate real-protocol receipts. Not a claim about market returns."""
    if not Path(dbpath).is_file():
        return {"status":"INCOMPLETE", "reasons":["missing_campaign_database"]}
    memory = Memory(dbpath)
    try:
        store = Playbook(memory)
        store.audit(project_id)
        batches = [dict(x) for x in memory.db.execute(
            "SELECT id,run_id,status,batch_number FROM v08_batches WHERE project_id=? "
            "ORDER BY batch_number", (project_id,))]
        reasons = []
        if len(batches) < min_rounds or len(responses) < min_rounds:
            reasons.append("fewer_than_required_rounds")
        role_records = []
        for b in batches:
            if b["status"] != "completed":
                reasons.append("incomplete_batch_"+str(b["batch_number"]))
            rows = [dict(x) for x in memory.db.execute(
                "SELECT r.role,r.model,r.state,r.id,r.prompt_sha256,"
                "p.original_raw_sha256,p.response_id,p.tool_call_id,p.body_json "
                "FROM v08_requests r LEFT JOIN v08_role_payloads p ON p.request_id=r.id "
                "WHERE r.run_id=? ORDER BY r.created,r.id",(b["run_id"],))]
            if {r["role"] for r in rows} != set(PHASES) or len(rows) != len(PHASES):
                reasons.append("missing_role_"+str(b["batch_number"]))
            for row in rows:
                if (row["state"] != "completed" or not row["response_id"]
                        or not row["tool_call_id"] or not row["original_raw_sha256"]
                        or not row["prompt_sha256"]):
                    reasons.append("missing_vendor_provenance_"+row["role"])
                role_records.append({"batch_number":b["batch_number"], "role":row["role"],
                                     "model":row["model"],"request_id":row["id"],
                                     "response_id":row["response_id"],
                                     "tool_call_id":row["tool_call_id"],
                                     "original_raw_sha256":row["original_raw_sha256"]})
            # The DB fact must be linked to one actual provider policy and
            # five original authenticated source events (no scripted fixtures).
            provenance=[json.loads(r[0]) for r in memory.db.execute(
                "SELECT payload FROM events WHERE run_id=? AND kind='v08_role_provenance'",
                (b["run_id"],))]
            if len(provenance) != len(PHASES) or any(
                    r.get("source") != "authenticated_llm_tool_call" for r in provenance):
                reasons.append("scripted_or_unattributed_model_roles_"+str(b["batch_number"]))
            policies=[json.loads(r[0]) for r in memory.db.execute(
                "SELECT payload FROM events WHERE run_id=? AND kind='v08_llm_endpoint_authorized'",
                (b["run_id"],))]
            if len(policies)!=1 or not policies[0].get("endpoint_policy_sha256"):
                reasons.append("missing_endpoint_authorization_"+str(b["batch_number"]))
        queued=memory.db.execute(
            "SELECT COUNT(*) FROM v08_queue WHERE project_id=? AND trial_id IS NOT NULL",
            (project_id,)).fetchone()[0]
        played=memory.db.execute(
            "SELECT COUNT(*) FROM v08_knowledge WHERE project_id=? AND evidence_grade='synthetic_process'",
            (project_id,)).fetchone()[0]
        revised=memory.db.execute(
            "SELECT COUNT(*) FROM v08_hypotheses WHERE project_id=? AND parent_id IS NOT NULL",
            (project_id,)).fetchone()[0]
        if queued < 1:
            reasons.append("no_original_model_formula_reached_demo_simulation")
        if played < 1:
            reasons.append("no_source_bound_playbook_revision")
        if revised < 1:
            reasons.append("no_feedback_driven_parent_hypothesis_revision")
        if not any(r.get("memory_used") for r in responses[1:]):
            reasons.append("no_cross_batch_memory_used")
        return {
            "status":"PASS_REAL_LLM_ENGINEERING_PROTOCOL" if not reasons else "INCOMPLETE",
            "reasons":sorted(set(reasons)),"batches":len(batches),
            "roles_verified":len(role_records),"role_provenance":role_records,
            "demo_trials":queued,"synthetic_playbook_entries":played,
            "parent_child_revisions":revised,"real_brain_simulations":0,
            "real_brain_submits":0,"verified_market_alpha_count":0,
            "alpha_performance_validated":False,
        }
    finally:
        memory.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",required=True)
    parser.add_argument("--database",required=True,help="A fresh, unused v0.8 SQLite path")
    parser.add_argument("--report",required=True,help="New JSON receipt path")
    parser.add_argument("--project-id",required=True)
    parser.add_argument("--objective",required=True)
    parser.add_argument("--batches",type=int,default=3)
    parser.add_argument("--max-simulations-per-batch",type=int,default=2)
    parser.add_argument("--max-tokens-per-batch",type=int,default=40000)
    parser.add_argument("--max-known-usd-per-batch",type=float,required=True)
    parser.add_argument("--max-wall-seconds",type=int,default=900)
    parser.add_argument("--allow-remote-llm",action="store_true")
    args=parser.parse_args(argv)
    report=Path(args.report).resolve()
    try:
        config=load_settings(args.config)
        target=Path(args.database).resolve()
        original=Path(config["database"]).resolve()
        if target.exists() or target==original or target.name==original.name or target.suffix not in (".db",".sqlite3"):
            raise ResearchError("真实 LLM 验收必须使用不存在的新研究数据库")
        if (config["mode"]!="demo" or config["safety"].get("live_research_enabled")
                or config["safety"].get("submission_enabled")):
            raise ResearchError("真实 LLM 验收禁止所有真实 BRAIN 与 Submit 权限")
        if not 3<=args.batches<=10:
            raise ResearchError("真实 LLM 验收至少三轮、最多十轮")
        if not 0<args.max_known_usd_per_batch<=100:
            raise ResearchError("必须设置每批有限、正值的 USD 保护")
        if not os.getenv("LLM_API_KEY"):
            raise ResearchError("没有当前已授权 LLM_API_KEY；停止真实 LLM 验收，不模拟真实模型")
        price=config.get("cost",{})
        if any(type(price.get(k)) not in (int,float) or price[k]<0 for k in
               ("input_per_million_usd","output_per_million_usd")):
            raise ResearchError("真实模型费用需要已审核的输入/输出 token 定价，未知账单不得被当成零")
        policy=validate_llm_endpoint(config["llm"],operator_confirmed=args.allow_remote_llm)
        config["database"]=str(target)
        config["_v08_remote_operator_confirmed"]=bool(args.allow_remote_llm)
        plan=make_plan(config,project_id=args.project_id,objective=args.objective,
            max_simulations_per_batch=args.max_simulations_per_batch,
            max_llm_calls_per_batch=6,max_wall_seconds=args.max_wall_seconds,
            max_tokens_per_batch=args.max_tokens_per_batch,
            max_known_usd_per_batch=args.max_known_usd_per_batch)
        outcomes=[]
        for _ in range(args.batches):
            # adapter=None = genuine HTTP tool call, never a script injection.
            outcome=execute_batch(config,plan,adapter=None)
            outcomes.append(outcome)
        tested=audit_real_rounds(target,args.project_id,outcomes,min_rounds=args.batches)
        tested.update({"protocol":"v08-genuine-model-demo-acceptance-v1",
                       "provider_policy_sha256":policy["policy_sha256"],
                       "project_plan_sha256":plan["plan_sha256"],
                       "checked_at":datetime.now(timezone.utc).isoformat(),
                       "scripted_model_injected":False,
                       "actual_provider_requests_intended":True,
                       "independent_market_validation":False})
    except Exception as err:
        tested={"protocol":"v08-genuine-model-demo-acceptance-v1",
                "status":"BLOCKED_OR_FAILED", "error_type":type(err).__name__,
                "reason":str(err)[:400], "real_brain_simulations":0,
                "real_brain_submits":0,"independent_market_validation":False}
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open("x",encoding="utf-8") as out:
        out.write(json.dumps(tested,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":tested["status"],"report":str(report),
                      "brain_simulations":0,"submissions":0},ensure_ascii=False))
    return 0 if tested["status"]=="PASS_REAL_LLM_ENGINEERING_PROTOCOL" else 2

if __name__=="__main__":
    raise SystemExit(main())
