"""Offline verified research-IS overfitting diagnostics.

This module never touches the independent final holdout or sends returns to a
model. The existing return_contract requires signed-off PIT/cost/calendar
metadata; self-asserted manifest metadata is NOT external market attestation.
"""
from __future__ import annotations

import json
from pathlib import Path

from .common import ResearchError, digest, dumps, now, research_context
from .memory import Memory
from .return_contract import review_return_document
from .campaign_core import CampaignStore, validate_campaign_plan
from .campaign_overfit import correlation_precheck, walk_forward_audit

MAX_DOCUMENT_BYTES = 10_000_000

def review_campaign_is(config, plan, documents, *,
                       train_size=504, test_size=126, min_folds=3):
    validate_campaign_plan(config, plan)
    if config["mode"] != "demo":
        raise ResearchError("本工具仅离线检查已提供的研究 IS 证据；不发起 BRAIN 连接")
    if not isinstance(documents, list) or not 1 <= len(documents) <= 100:
        raise ResearchError("证据文件数量必须在 1..100 内")
    seen = set()
    verified = []
    for item in documents:
        file = Path(item).expanduser().resolve()
        if file in seen or not file.is_file() or file.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ResearchError("证据文件不可用/重复/过大：" + str(file))
        seen.add(file)
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError,UnicodeError,OSError) as error:
            raise ResearchError("研究证据 JSON 无效") from error
        series, status = review_return_document(doc)
        if series is None:
            raise ResearchError("研究样本收益协议验证失败：" + dumps(status["reasons"]))
        if series.synthetic:
            raise ResearchError("Demo 合成收益不能作为真实市场过拟合诊断证据")
        verified.append(series)
    memory = Memory(config["database"])
    try:
        store = CampaignStore(memory)
        project = store.get(plan["campaign_id"])
        if project["plan_hash"] != plan["plan_sha256"]:
            raise ResearchError("campaign 冻结计划不匹配")
        available = {x["id"]: x for x in store.rows(plan["campaign_id"])}
        by_trial = {v["trial_id"]: v for v in available.values() if v["trial_id"]}
        results = []
        for series in verified:
            item = available.get(series.candidate_id) or by_trial.get(series.candidate_id)
            if item is None or item["trial_id"] is None:
                raise ResearchError("证据对应的候选尚未登记为已模拟的研究项")
            trial = memory.trial(item["trial_id"])
            if trial["context"] != research_context(config):
                raise ResearchError("证据候选属于不同研究范围")
            wfa = walk_forward_audit(series,train_size=train_size,test_size=test_size,
                                     min_folds=min_folds)
            corr = correlation_precheck(series,
                       [x for x in verified if x is not series],
                       threshold=plan["max_return_correlation"],
                       min_overlap=config["policy"]["min_pnl_overlap"])
            safe = {"idea_id": item["id"], "trial_id": item["trial_id"],
                    "research_is_contract_sha256": digest(series.contract),
                    "document_sha256": series.input_sha256,
                    "diagnostic": {"walk_forward": wfa, "correlation": corr},
                    "independent_holdout_validated": False,
                    "fit_for_final_selection": False}
            results.append(safe)
        # Exact repeated invocation has no side effects. Novel receipts are
        # append-only and include evidence IDs, thresholds and a frozen hash.
        report_hash=digest({"campaign_id":plan["campaign_id"],
                            "plan_sha256":plan["plan_sha256"],
                            "results":results,"policy_hash":digest(config["policy"])})
        def write():
            memory.db.execute("""CREATE TABLE IF NOT EXISTS campaign_diagnostics(
                  id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,report_json TEXT NOT NULL,
                  created TEXT NOT NULL)""")
            memory.db.execute("""CREATE TRIGGER IF NOT EXISTS campaign_diagnostics_no_update
                  BEFORE UPDATE ON campaign_diagnostics
                  BEGIN SELECT RAISE(ABORT,'immutable diagnostics'); END""")
            memory.db.execute("""CREATE TRIGGER IF NOT EXISTS campaign_diagnostics_no_delete
                  BEFORE DELETE ON campaign_diagnostics
                  BEGIN SELECT RAISE(ABORT,'immutable diagnostics'); END""")
            if not memory.db.execute(
                    "SELECT 1 FROM campaign_diagnostics WHERE id=?", (report_hash,)).fetchone():
                memory.db.execute(
                    "INSERT INTO campaign_diagnostics VALUES(?,?,?,?)",
                    (report_hash,plan["campaign_id"],dumps(results),now()))
                store._log(plan["campaign_id"],"overfit_diagnostics",
                           {"report_sha256":report_hash,
                            "IS_evidence_count":len(results),
                            "independent_holdout_validated":False,
                            "formula_selection": "diagnostic_only"})
        memory.transaction(write)
        return {"campaign_id":plan["campaign_id"], "report_sha256":report_hash,
                "results":results, "final_holdout_revealed":False,
                "automated_quality_goal_completion":False}
    finally:
        memory.close()
