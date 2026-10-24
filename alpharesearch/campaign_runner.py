"""L1 breadth -> L2 selected depth -> L3 feedback / next-generation scheduler.

New bounded Engine run per invocation; durable campaign queue spans runs. Live
creation is deliberately DISABLED in this offline deliverable.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .common import ResearchError, digest, dumps, now
from .discovery import DiscoveryAgent, validate_idea, approved_fields
from .engine import Engine
from .memory import Memory
from .campaign_core import (CampaignStore, create_campaign_plan,
                            validate_campaign_plan, validate_limits)

def _metrics_good(metrics, policy):
    if not isinstance(metrics, dict):
        return False
    sh, ft = metrics.get("sharpe"), metrics.get("fitness")
    return (type(sh) in (float, int) and math.isfinite(sh)
            and type(ft) in (float, int) and math.isfinite(ft)
            and sh >= policy["min_sharpe"] and ft >= policy["min_fitness"])

class CampaignDemoDiscovery:
    """Diverse deterministic engineering fixtures; no empirical alpha claims."""
    _forms = [
        ("rank(ts_mean(returns,{w}))", "momentum"),
        ("-rank(ts_delta(close,{w}))", "reversion"),
        ("-rank(ts_std_dev(returns,{w}))", "volatility"),
        ("-rank(ts_corr(close,volume,{w}))", "liquidity"),
        ("rank(ts_rank(volume,{w}))", "momentum"),
        ("-rank(ts_zscore(close,{w}))", "reversion"),
        ("rank(ts_sum(returns,{w}))", "momentum"),
        ("-rank(ts_decay_linear(volume,{w}))", "liquidity"),
    ]
    def __init__(self, config):
        self.config = config

    def generate(self, plan, feedback, cycle):
        approved = approved_fields(self.config)
        if "price_volume" not in plan["domains"] or not all(
                name in approved for name in ("returns", "close", "volume")):
            return []
        maximum = self.config["rules"]["max_window"]
        count = plan["ideas_per_cycle"]
        items = []
        start = (cycle-1)*count
        for index in range(start, start+count):
            form, family = self._forms[index % len(self._forms)]
            window = 5 + index // len(self._forms)
            if window > maximum:
                break
            items.append({
                "title": "Campaign synthetic fixture %d" % (index+1),
                "domain": "price_volume", "mechanism": "Only an offline scheduling fixture",
                "expected_sign": "conditional", "horizon_days": 5,
                "falsification": "Independent empirical validation not performed",
                "expression": form.format(w=window),
                "source": "DEMO synthetic fixture; not market evidence",
                "family": family, "parent_idea_id": None,
                "change": "Independent deterministic test formula"})
        return items

class CampaignRunner:
    def __init__(self, engine, plan, store, limits, *, generator=None):
        validate_campaign_plan(engine.config, plan)
        if engine.config["mode"] != "demo":
            raise ResearchError("offline campaign 禁止连接 BRAIN；live 需额外单独授权与验收")
        if engine.context != plan["context"]:
            raise ResearchError("campaign 研究范围与执行上下文不一致")
        self.engine, self.plan, self.store, self.limits = engine, plan, store, limits
        self.cid = plan["campaign_id"]
        self.generator = generator
        self.started = time.monotonic()
        self.base_in_run = 0
        self.depth_in_run = 0
        self.actions = 0

    def _record(self, name, payload):
        self.engine.memory.transaction(lambda: self.store._log(self.cid, name, payload, self.engine.run_id))

    def _available(self):
        return max(0, self.engine.simulation_limit -
                   self.engine.memory.run(self.engine.run_id)["simulations"])

    def _resources(self, *, require_llm=False):
        if time.monotonic()-self.started >= self.limits["max_wall_seconds"]:
            return "wall_limit"
        if self.actions >= self.limits["max_steps"]:
            return "step_limit"
        if self._available() <= 0:
            return "batch_simulation_limit"
        usage = self.store.observed_llm_cost(self.engine.run_id)
        if usage["unknown_tokens"] or (self.limits["max_known_usd"] is not None
                                        and usage["unknown_usd"]):
            # No guessed zero usage. When a USD budget is configured, unknown
            # pricing also blocks further model work; without a USD budget,
            # unknown USD is reported but does not masquerade as a known zero.
            return "unknown_llm_usage_requires_review"
        if usage["known_tokens"] >= self.limits["max_input_output_tokens"]:
            return "token_limit"
        if self.limits["max_known_usd"] is not None and usage["known_usd"] >= self.limits["max_known_usd"]:
            return "usd_limit"
        if require_llm and self.engine.memory.run(self.engine.run_id)["llm_calls"] >= self.limits["max_llm_calls"]:
            return "llm_call_limit"
        return None

    def _lifetime_guard(self):
        """Separate campaign-wide protective ceilings from per-run allowances."""
        totals = self.store.cumulative_resources(self.cid)
        for key, observed, uncertainty in (
            ("max_total_llm_calls", "llm_calls", None),
            ("max_total_tokens", "known_tokens", "unknown_token_calls"),
            ("max_total_known_usd", "known_usd", "unknown_usd_calls"),
        ):
            cap = self.plan.get(key)
            if cap is None:
                continue
            if uncertainty is not None and totals[uncertainty]:
                self.store.control(self.cid, "resource",
                    "unreconciled_" + uncertainty + "_before_" + key)
                return "paused_resource_unknown_cost"
            if totals[observed] >= cap:
                self.store.control(self.cid, "resource", "lifetime_ceiling_" + key)
                return "paused_resource_" + key
        return None

    def _cycle(self):
        return self.store.db.execute(
            "SELECT COALESCE(MAX(cycle),0) FROM campaign_generations WHERE campaign_id=?",
            (self.cid,)).fetchone()[0]

    def _feedback(self):
        items = self.store.rows(self.cid)
        newest = [v for v in items if v["state"] in ("evaluated", "promising", "rejected", "deferred")]
        families = {}
        for v in newest:
            family = v["idea"].get("family", "unknown")
            stat = families.setdefault(family, {"tested": 0, "weak": 0})
            if v["trial_id"] and self.engine.memory.latest(v["trial_id"]) is not None:
                stat["tested"] += 1
                if not _metrics_good((self.engine.memory.latest(v["trial_id"]) or {}).get("metrics"),
                                     self.engine.config["policy"]):
                    stat["weak"] += 1
        tail = []
        for v in newest[-12:]:
            result = self.engine.memory.latest(v["trial_id"]) if v["trial_id"] else None
            tail.append({"idea_id": v["id"], "state": v["state"],
                         "family": v["idea"].get("family"),
                         "reason": v["reason"], "parent_idea_id": v["parent_id"],
                         "expression": v["idea"].get("expression"),
                         "metrics": {k: (result or {}).get("metrics", {}).get(k)
                                     for k in ("sharpe", "fitness", "turnover")}})
        # Explicit allowlist: never add PnL, final holdout or raw platform JSON.
        return {"family_summary": families, "recent": tail, "total_hypotheses": len(items),
                "interpretation": "IS search only; synthetic Demo data is not evidence"}

    def _parents(self):
        return {v["id"] for v in self.store.rows(self.cid)
                if v["trial_id"] and v["state"] in ("evaluated", "promising", "deferred") and
                self.engine.memory.latest(v["trial_id"]) is not None}

    def _register_batch(self, ideas, cycle, *, original_ids=None):
        if not isinstance(ideas, list) or len(ideas) > self.plan["ideas_per_cycle"]:
            raise ResearchError("生成批次超出冻结上限")
        parents = self._parents()
        for index, idea in enumerate(ideas):
            original = original_ids[index] if original_ids else "%d:%d" % (cycle, index)
            try:
                valid = validate_idea(self.engine, idea, self.plan["domains"], parents)
                failure = None
            except ResearchError as error:
                valid, failure = None, str(error)
            ident, created = self.store.add(self.cid, idea, cycle, validated=valid,
                                            reason=failure, original_id=original)
            if not created or valid is None:
                continue
            match = self.store.external_duplicate(self.plan["external_libraries"],
                                                   self.engine.context, valid.fingerprint)
            if match:
                self.store.move(ident, "deferred", "l1",
                                "external_library_duplicate_requires_review:" +
                                match["library_sha256"] + ":" + match["trial_id"])
            else:
                self.store.move(ident, "queued", "l1", "L1_AST_and_economic_hypothesis_validated")
        return len(ideas)

    def _generate(self):
        if self.generator is None:
            if self.plan["generator"] == "demo":
                self.generator = CampaignDemoDiscovery(self.engine.config)
            else:
                self.generator = DiscoveryAgent(self.engine)
        model_request = self.plan["generator"] == "llm"
        if self._resources(require_llm=model_request):
            return False
        cycle_next = self._cycle()+1
        feedback = self._feedback()
        # Plain JSON allowlist remains small after many hundreds of batches.
        request_hash = digest({"cycle": cycle_next, "feedback": feedback,
                               "objective": self.plan["objective"]})
        cycle = self.store.begin_generation(self.cid, self.engine.run_id, request_hash)
        try:
            if model_request:
                # No change to legacy tool calling. Optional role models are frozen
                # as part of [llm] identity by the existing snapshot().
                llm = self.engine.config["llm"]
                if isinstance(self.generator, DiscoveryAgent):
                    role = "reasoning_model" if feedback["recent"] else "fast_model"
                    selected = llm.get(role, llm["model"])
                    if not isinstance(selected, str) or not selected:
                        raise ResearchError("无效的 LLM 任务模型配置")
                    self.generator.model = selected
                    self._record("model_route", {"task": "feedback_revision" if feedback["recent"] else "exploration",
                                                "role": role, "model": selected})
            batch = self.generator.generate(self.plan, feedback, cycle)
            count = self._register_batch(batch, cycle)
        except Exception:
            self.store.end_generation(self.cid, cycle, "failed", 0)
            # Do not retry a non-idempotent API request automatically.
            raise
        self.store.end_generation(self.cid, cycle, "completed", count)
        self._record("memory_checkpoint", {"cycle": cycle,
                                            "summary_sha256": digest(feedback),
                                            "queue_size": len(self.store.rows(self.cid))})
        return count > 0

    def _family_blocked(self, item):
        limit = self.plan["family_failure_limit"]
        family = item["idea"].get("family")
        cycle = self._cycle()
        past = [r for r in self.store.rows(self.cid)
                if r["idea"].get("family") == family and r["trial_id"] and r["id"] != item["id"]
                and r["state"] in ("evaluated", "promising")]
        if len(past) < limit:
            return False
        last = past[-limit:]
        if all(not _metrics_good((self.engine.memory.latest(r["trial_id"]) or {}).get("metrics"),
                                 self.engine.config["policy"]) for r in last):
            # A short cooldown, not permanent elimination of an economic family.
            end = last[-1]["cycle"] + self.plan["family_cooldown_cycles"]
            if cycle <= end:
                self._record("family_breaker", {"family": family, "until_cycle": end,
                                                "recent_failures": limit})
                self.store.move(item["id"], "deferred", "l1",
                                "family_circuit_open_until_cycle_" + str(end))
                return True
        return False

    def _wake_deferred(self):
        cycle = self._cycle()
        for item in self.store.rows(self.cid, state="deferred"):
            prefix = "family_circuit_open_until_cycle_"
            reason = item["reason"]
            if reason.startswith(prefix) and cycle > int(reason[len(prefix):]):
                self.store.move(item["id"], "queued", "l1", "family_cooldown_elapsed")

    def _run_base(self, item):
        self.store.move(item["id"], "testing", "l1", "L1_base_simulation_prepared")
        idea = item["idea"]
        try:
            # Mandatory revalidation at dispatch (including feedback revisions).
            val = validate_idea(self.engine, idea, self.plan["domains"], self._parents())
            if val.fingerprint != item["fingerprint"]:
                raise ResearchError("公式的冻结指纹在登记后改变")
        except ResearchError as err:
            self.store.move(item["id"], "rejected", "l1", str(err))
            return
        existing = self.engine.memory.existing_trial(self.engine.context, val.fingerprint)
        if existing:
            trial = self.engine.memory.trial(existing)
            if trial["state"] != "completed" or not self.engine.memory.latest(existing):
                self.store.move(item["id"], "deferred", "l1",
                                "existing_unfinished_trial_requires_owner_recovery")
                return
            tid = existing
            self._record("reuse", {"idea_id": item["id"], "trial_id": tid, "new_simulations": 0})
        else:
            source = "campaign:" + self.cid + ":" + item["id"]
            parent = self.store.one(idea["parent_idea_id"]) if idea["parent_idea_id"] else None
            parent_tid = parent["trial_id"] if parent else None
            self.engine.memory.consume(self.engine.run_id, "proposals",
                                       self.engine.config["rules"]["max_proposals_per_run"])
            hypothesis = dumps({k: idea[k] for k in ("title", "mechanism", "expected_sign",
                            "horizon_days", "falsification", "change", "source")})
            tid, created = self.engine.memory.add_trial(
                self.engine.run_id, {"expression": idea["expression"], "hypothesis": hypothesis,
                                     "source": source},
                val, self.engine.context, parent_id=parent_tid)
            if not created and self.engine.memory.trial(tid)["state"] != "completed":
                self.store.move(item["id"], "deferred", "l1", "concurrent_or_unfinished_trial")
                return
            if created:
                parent_intent_id = None
                if parent_tid:
                    refs = self.engine.memory.intents(trial_id=parent_tid)
                    parent_intent_id = refs[-1]["id"] if refs else None
                self.engine.memory.add_intent(self.engine.run_id, tid, hypothesis,
                                              source, parent_intent_id)
                outcome = self.engine.execute(tid)
                if outcome.get("state") != "completed":
                    raise ResearchError("基础实验尚未完成；不得新建替代性 POST")
        self.store.move(item["id"], "evaluated", "l1",
                        "L1_base_feedback_only;no_independent_holdout",
                        trial_id=tid)
        self.base_in_run += 1
        self._record("l1_result", {"idea_id": item["id"], "trial_id": tid,
                                  "metrics_sha256": digest((self.engine.memory.latest(tid) or {}).get("metrics", {})),
                                  "synthetic": True, "market_performance": False})

    def _run_depth(self, item):
        tid = item["trial_id"]
        if not tid or self.engine.memory.trial(tid)["state"] != "completed":
            self.store.move(item["id"], "deferred", "l2", "base_result_missing")
            return
        self.store.move(item["id"], "testing", "l2", "L2_selected_depth")
        expression = item["idea"]["expression"]
        neighbors = self.engine.validator.neighbors(expression)[:self.plan["max_sensitivity_variants"]]
        pending = False
        for candidate in neighbors:
            if self.depth_in_run >= self.plan["max_depth_per_batch"]:
                pending = True
                break
            val = self.engine.validator.validate(candidate)
            link = self.store.db.execute(
                "SELECT status,trial_id FROM campaign_depth WHERE campaign_id=? "
                "AND idea_id=? AND formula_hash=?", (self.cid, item["id"], val.fingerprint)).fetchone()
            if link and link["status"] == "completed":
                continue
            if self._available() <= 0:
                pending = True
                break
            existing = self.engine.memory.existing_trial(self.engine.context, val.fingerprint)
            if existing and self.engine.memory.trial(existing)["state"] != "completed":
                pending = True
                self._record("depth_blocked", {"idea_id": item["id"],
                                               "fingerprint": val.fingerprint,
                                               "reason": "external_or_prior_run_unfinished"})
                break
            if existing:
                neighbor_tid = existing
            else:
                self.engine.memory.consume(self.engine.run_id, "proposals",
                                           self.engine.config["rules"]["max_proposals_per_run"])
                neighbor_tid, created = self.engine.memory.add_trial(
                    self.engine.run_id,
                    {"expression": candidate, "hypothesis": dumps({"parent_idea_id": item["id"]}),
                     "source": "campaign_deterministic_sensitivity"},
                    val, self.engine.context, kind="sensitivity", parent_id=tid)
                if created:
                    outcome = self.engine.execute(neighbor_tid)
                    if outcome.get("state") != "completed":
                        raise ResearchError("参数敏感性实验未完成；停止进一步请求")
            # Link across runs without modifying the old trial or its frozen plan.
            self.engine.memory.transaction(lambda: self.engine.memory.event(
                self.engine.run_id, "sensitivity_link",
                {"parent_id": tid, "neighbor_id": neighbor_tid, "campaign": self.cid}))
            for intent in self.engine.memory.intents(trial_id=tid):
                self.engine.memory.link_sensitivity(intent["id"], neighbor_tid,
                    digest({"expressions": self.engine.validator.neighbors(expression),
                            "scope": self.engine.config["scope"]}))
            def record():
                self.store.db.execute(
                    "INSERT OR IGNORE INTO campaign_depth VALUES(?,?,?,?,?,?)",
                    (self.cid, item["id"], val.fingerprint, neighbor_tid, "completed", now()))
                self.store._log(self.cid, "depth_result",
                                {"idea_id": item["id"], "neighbor_trial_id": neighbor_tid,
                                 "fingerprint": val.fingerprint}, self.engine.run_id)
            self.engine.memory.transaction(record)
            self.depth_in_run += int(existing is None)
            self.actions += 1
        completed = self.store.db.execute(
            "SELECT count(*) FROM campaign_depth WHERE campaign_id=? AND idea_id=? AND status='completed'",
            (self.cid, item["id"])).fetchone()[0]
        complete = not pending and completed >= len(neighbors)
        if not complete:
            self.store.move(item["id"], "evaluated", "l2", "L2_partial_depth_more_batches_needed")
            return
        # Original evaluation contract remains untouched. A synthetic demo score
        # can only mark an engineering candidate, never empirical performance.
        evaluation = self.engine.evaluate_trial(tid)
        evidence = (self.engine.memory.latest(tid) or {}).get("metrics", {})
        passing = _metrics_good(evidence, self.engine.config["policy"])
        passing = passing and completed >= self.engine.config["policy"].get("min_sensitivity_neighbors",2)
        # Live independent proof/correlation/holdout is deliberately unavailable here.
        target_state = "promising" if passing and self.engine.config["mode"] == "demo" else "evaluated"
        self.store.move(item["id"], target_state, "l3",
                        "synthetic_engineering_candidate_not_market_alpha" if target_state == "promising"
                        else "L2_insufficient_or_unverified_evidence")
        self._record("l2_assessment", {
            "idea_id": item["id"], "trial_id": tid,
            "fixed_evaluator_rejections": evaluation.get("rejections", []),
            "missing": evaluation.get("missing", []),
            "independent_holdout": False, "walk_forward_market_proven": False,
            "return_correlation_precheck": "unavailable_without_verified_IS_returns",
            "synthetic": True})

    def _recover_stale(self):
        # Never resubmit queued trials belonging to an old run, and never
        # silently replay a task that may have performed a remote write.
        stuck = self.store.rows(self.cid, state="testing")
        for item in stuck:
            val = self.engine.validator.validate(item["idea"]["expression"])
            trial_id = item["trial_id"] or self.engine.memory.existing_trial(
                self.engine.context, val.fingerprint)
            if trial_id and self.engine.memory.trial(trial_id)["state"] == "completed" and (
                    self.engine.memory.latest(trial_id) is not None):
                self.store.move(item["id"], "evaluated", item["phase"],
                                "reconciled_completed_result_without_new_POST", trial_id=trial_id)
            else:
                raise ResearchError("存在未确认的旧实验；必须人工对账，禁止自动重复 POST")

    def _pick_depth(self):
        if self.depth_in_run >= self.plan["max_depth_per_batch"]:
            return None
        base_total = sum(x["trial_id"] is not None and x["phase"] in ("l1", "l2", "l3")
                         for x in self.store.rows(self.cid)
                         if x["state"] in ("evaluated", "promising"))
        if base_total < self.plan["breadth_before_depth"]:
            return None
        untested = self.store.rows(self.cid, state="queued", phase="l1")
        if untested and self.base_in_run < min(2, self.limits["max_new_simulations"]):
            return None
        for item in self.store.rows(self.cid, state="evaluated"):
            if item["phase"] not in ("l1", "l2") or not item["trial_id"]:
                continue
            # When audited research-IS evidence exists, reject redundant
            # return streams BEFORE allocating more depth simulations.
            table = self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='campaign_diagnostics'").fetchone()
            if table:
                for receipt in self.store.db.execute(
                    "SELECT report_json FROM campaign_diagnostics WHERE campaign_id=? "
                    "ORDER BY created DESC,id DESC", (self.cid,)):
                    results = json.loads(receipt["report_json"])
                    evidence = next((v for v in results if v["idea_id"] == item["id"]),None)
                    if evidence is None:
                        continue
                    if evidence["diagnostic"]["correlation"]["status"] == "reject":
                        self.store.move(item["id"], "deferred", "l3",
                                        "audited_verified_IS_return_correlation_redundant")
                    break
                if self.store.one(item["id"])["state"] != "evaluated":
                    continue
            metrics = (self.engine.memory.latest(item["trial_id"]) or {}).get("metrics", {})
            if _metrics_good(metrics, self.engine.config["policy"]):
                return item
            if item["phase"] == "l1":
                self.store.move(item["id"], "deferred", "l3",
                                "L1_weak_base_metrics;no_depth_budget_consumed")
        return None

    def step(self):
        self._wake_deferred()
        queue = [v for v in self.store.rows(self.cid, state="queued") if v["phase"] == "l1"]
        # Reserve L1 breadth first; L2 at most max_depth_per_batch.
        if self._available() and self.base_in_run >= min(2, self.limits["max_new_simulations"]):
            item = self._pick_depth()
            if item:
                self._run_depth(item)
                return "depth"
        if queue and self._available():
            for item in queue:
                if not self._family_blocked(item):
                    self._run_base(item)
                    return "base"
        # L3: if queue empty, attempt selected depth before new generation.
        if self._available():
            item = self._pick_depth()
            if item:
                self._run_depth(item)
                return "depth"
        if self._available() and (
                len(self.store.rows(self.cid, state="queued")) < self.plan["queue_low_watermark"]):
            return "generation" if self._generate() else "idle"
        return "idle"

    def run(self):
        if self.store.get(self.cid)["status"] != "active":
            raise ResearchError("campaign 已暂停/结束；需要显式恢复")
        self._recover_stale()
        reason = "step_limit"
        while True:
            if self.store.get(self.cid)["status"] != "active":
                reason = "operator_stop"
                break
            cumulative = self.store.cumulative(self.cid)
            cap = self.plan["max_total_simulations"]
            if cap is not None and cumulative >= cap:
                self.store.control(self.cid, "complete", "finite_lifecycle_cap")
                reason = "finite_lifecycle_cap"
                break
            lifetime_block = self._lifetime_guard()
            if lifetime_block:
                reason = lifetime_block
                break
            block = self._resources()
            if block:
                reason = block
                break
            result = self.step()
            if result == "idle":
                reason = "no_new_work_or_authorized_fields"
                break
            self.actions += 1
        self._record("batch_end", {"run_id": self.engine.run_id, "reason": reason,
                                   "simulations": self.engine.memory.run(self.engine.run_id)["simulations"],
                                   "base_actions": self.base_in_run, "depth_actions": self.depth_in_run,
                                   "llm_cost": self.store.observed_llm_cost(self.engine.run_id)})
        report = self.store.summary(self.cid)
        report["quality_goal_status"] = (
            "independent_attestation_unavailable_in_demo"
            if self.plan.get("quality_goal_count") is not None else "not_configured")
        report.update({"batch_run_id": self.engine.run_id, "batch_stop_reason": reason,
                       "counts_this_batch": self.engine.memory.run(self.engine.run_id)["simulations"],
                       "engineering_only": True, "real_brain_simulations": 0, "brain_submits": 0,
                       "audit": self.store.audit_queue(self.cid)})
        return report

def execute_campaign(config, plan, limits=None, *, generator=None, simulator=None):
    """One isolated, bounded batch. Caller should hold the existing worker lock.

    Never constructs BrainSimulator; cannot issue BRAIN Simulation or Submit.
    """
    validate_campaign_plan(config, plan)
    if config["mode"] != "demo":
        raise ResearchError("本交付仅运行离线 Demo，不连接真实 BRAIN")
    limits = validate_limits(config, limits or {})
    # Existing simulator injection is for offline regression tests ONLY.
    first = Memory(config["database"])
    try:
        store = CampaignStore(first)
        store.create(plan)
        store.audit_queue(plan["campaign_id"])
        if store.get(plan["campaign_id"])["status"] != "active":
            raise ResearchError("campaign 非 active，必须由操作员恢复")
        unknown = first.db.execute(
            "SELECT 1 FROM campaign_generations WHERE campaign_id=? AND status='started'",
            (plan["campaign_id"],)).fetchone()
        if unknown:
            store.control(plan["campaign_id"], "fault", "model_result_unknown_require_operator")
            raise ResearchError("模型中断未知，显式 resume 后才可新建请求")
        cap = plan["max_total_simulations"]
        if cap is not None:
            remaining = cap-store.cumulative(plan["campaign_id"])
            if remaining <= 0:
                store.control(plan["campaign_id"], "complete", "finite_lifecycle_cap")
                report = store.summary(plan["campaign_id"])
                report.update({"batch_stop_reason": "finite_lifecycle_cap",
                               "real_brain_simulations": 0, "brain_submits": 0})
                return report
            limits = {**limits, "max_new_simulations": min(limits["max_new_simulations"],remaining)}
    finally:
        first.close()
    # Each invocation gets a NEW bounded run; old frozen runs never change.
    engine = Engine(config, simulator=simulator, simulation_limit=limits["max_new_simulations"])
    try:
        store = CampaignStore(engine.memory)
        store.begin_run(plan["campaign_id"], engine.run_id, limits)
        engine.memory.transaction(lambda: engine.memory.event(
            engine.run_id, "research_arm", {"agent": "campaign_"+plan["generator"],
                                           "campaign_id": plan["campaign_id"],
                                           "automatic_submit": False}))
        loop = CampaignRunner(engine, plan, store, limits, generator=generator)
        try:
            return loop.run()
        except Exception as error:
            if store.get(plan["campaign_id"])["status"] == "active":
                store.control(plan["campaign_id"], "fault",
                              "batch_error_requires_review:" + type(error).__name__)
            store._log(plan["campaign_id"], "fault_detail",
                       {"error_type": type(error).__name__, "run_id": engine.run_id})
            raise
    finally:
        engine.memory.close()

def control_campaign(config, plan, action, reason="operator"):
    validate_campaign_plan(config, plan)
    memory = Memory(config["database"])
    try:
        store = CampaignStore(memory)
        if action == "resume":
            store.abort_unknown_generations(plan["campaign_id"])
        outcome = store.control(plan["campaign_id"], action, reason)
        store.audit_queue(plan["campaign_id"])
        return {"campaign_id": plan["campaign_id"], "status": outcome}
    finally:
        memory.close()

def import_legacy_ideas(config, plan, legacy_study_id):
    """Append-only migration of recorded old ideas; original run stays frozen.

    Incomplete tasks owned by the legacy run are NOT moved or retried. Only an
    idea without a completed prior simulation is scheduled in the new run;
    complete results may be safely reused by exact same-context fingerprint.
    """
    validate_campaign_plan(config, plan)
    if config["mode"] != "demo":
        raise ResearchError("迁移需在离线环境进行；禁止 BRAIN")
    # A repeated migration of a fully attested legacy study is a read-only
    # idempotent no-op: do not create another empty run or event.
    prior_memory = Memory(config["database"])
    try:
        existing_store = CampaignStore(prior_memory)
        if prior_memory.db.execute(
                "SELECT 1 FROM campaign_projects WHERE campaign_id=?",
                (plan["campaign_id"],)).fetchone():
            record = existing_store.get(plan["campaign_id"])
            if record["plan_hash"] != plan["plan_sha256"]:
                raise ResearchError("迁移目标已绑定其他冻结 campaign 计划")
            for entry in prior_memory.db.execute(
                    "SELECT payload FROM events WHERE kind='campaign_legacy_import' ORDER BY id"):
                payload = json.loads(entry["payload"])
                if (payload.get("campaign_id") == plan["campaign_id"] and
                        payload.get("legacy_study_id") == legacy_study_id):
                    existing_store.audit_queue(plan["campaign_id"])
                    return {"legacy_run_id": payload["old_run_id"],
                            "imported": 0, "already_imported": True,
                            "new_campaign": existing_store.summary(plan["campaign_id"]),
                            "old_run_unchanged": True, "brain_simulation_posts": 0}
    finally:
        prior_memory.close()
    engine = Engine(config, simulation_limit=0)
    try:
        store = CampaignStore(engine.memory)
        store.create(plan)
        record = engine.memory.db.execute(
            "SELECT run_id,plan_hash FROM autonomous_studies WHERE study_id=?",
            (legacy_study_id,)).fetchone()
        if not record:
            raise ResearchError("旧自主研究不存在；请提供本地原始 SQLite")
        legacy_run, old_hash = record
        original = engine.memory.run(legacy_run)
        if json.loads(original["snapshot"]).get("mode") != "demo":
            raise ResearchError("仅能离线迁移 Demo 历史候选")
        ideas, states = {}, {}
        for row in engine.memory.db.execute(
                "SELECT kind,payload FROM events WHERE run_id=? AND kind IN "
                "('autonomy_idea','autonomy_idea_state') ORDER BY id", (legacy_run,)):
            item = json.loads(row["payload"])
            if row["kind"] == "autonomy_idea":
                ideas[item["idea_id"]] = item
            else:
                states.setdefault(item["idea_id"], {}).update(item)
        if not ideas:
            raise ResearchError("旧 Run 中没有候选事件")
        # Topological order preserves arbitrarily deep parent chains; refuse
        # missing parents/cycles rather than silently changing genealogy.
        pending = dict(ideas)
        ordered = []
        finished = set()
        while pending:
            ready = sorted((row for row in pending.values()
                if row["idea"].get("parent_idea_id") is None or
                row["idea"]["parent_idea_id"] in finished),
                key=lambda row: row["idea_id"])
            if not ready:
                raise ResearchError("历史假设谱系缺失、循环或事件不完整；停止迁移")
            for row in ready:
                ordered.append(row)
                finished.add(row["idea_id"])
                pending.pop(row["idea_id"])
        mapped, imported = {}, 0
        from .discovery import validate_idea
        for item in ordered:
            old_id = item["idea_id"]
            idea = dict(item["idea"])
            if idea.get("parent_idea_id") is not None:
                parent = mapped.get(idea["parent_idea_id"])
                if parent is None:
                    raise ResearchError("旧父假设缺失；不得虚构谱系")
                idea["parent_idea_id"] = parent
            parents = {x["id"] for x in store.rows(plan["campaign_id"])}
            try:
                valid = validate_idea(engine, idea, plan["domains"], parents)
                reason = None
            except ResearchError as err:
                valid, reason = None, "legacy_validation_failed:"+str(err)
            cid, created = store.add(plan["campaign_id"], idea, 0,
                                     validated=valid, reason=reason, original_id=old_id)
            mapped[old_id] = cid
            if not created or valid is None:
                continue
            old_trial = states.get(old_id, {}).get("trial_id")
            completed = bool(old_trial and
                             engine.memory.trial(old_trial)["state"] == "completed" and
                             engine.memory.latest(old_trial))
            if completed:
                store.move(cid, "queued", "l1", "legacy_import_complete_result")
                store.move(cid, "testing", "l1", "legacy_result_reconciliation")
                store.move(cid, "evaluated", "l1", "legacy_completed_result_reused",
                           trial_id=old_trial)
            elif old_trial:
                store.move(cid, "deferred", "l1",
                           "legacy_trial_unfinished_requires_original_run_recovery")
            else:
                store.move(cid, "queued", "l1", "legacy_untested_queued_candidate")
            imported += 1
        engine.memory.transaction(lambda: store._log(
            plan["campaign_id"], "legacy_import",
            {"legacy_study_id": legacy_study_id, "old_plan_hash": old_hash,
             "old_run_id": legacy_run, "legacy_snapshot_hash": original["snapshot_hash"],
             "imported": imported, "source_event_hash": engine.memory.verify()["head"]}))
        return {"legacy_run_id": legacy_run, "imported": imported,
                "new_campaign": store.summary(plan["campaign_id"]),
                "old_run_unchanged": True, "brain_simulation_posts": 0}
    finally:
        engine.memory.close()
