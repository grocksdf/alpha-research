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
from .formula_quality import find_equivalent, validate_units
from .proposal_audit import verify_source, verify_source_hashes
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

class CampaignDiscoveryAgent(DiscoveryAgent):
    """Keep v0.6.1 real tool calls; enforce campaign-specified resource envelope."""

    def __init__(self,engine,plan,limits,started):
        super().__init__(engine)
        self.campaign_plan = plan
        self.campaign_limits = limits
        self.campaign_started = started

    def complete(self,messages,tools):
        prompt = dumps(messages)
        if len(prompt) > self.campaign_plan["llm_max_prompt_chars"]:
            raise ResearchError("LLM 输入上下文超出冻结长度限制；需要压缩反馈")
        elapsed = time.monotonic() - self.campaign_started
        remaining = self.campaign_limits["max_wall_seconds"] - elapsed
        if remaining < 1:
            raise ResearchError("本次执行剩余时间不足，禁止开始新的 LLM 请求")
        output_max = self.campaign_plan["llm_max_output_tokens"]
        old_output = self.extra_params.get("max_tokens")
        if old_output is not None:
            output_max = min(output_max,old_output)
        self.extra_params = {**self.extra_params,"max_tokens":output_max}
        original_timeout = self.engine.config["llm"]["timeout_seconds"]
        timeout = min(float(original_timeout),float(remaining))
        if timeout <= 0:
            raise ResearchError("运行级超时无效")
        if hasattr(self.api,"transport") and hasattr(self.api.transport,"timeout"):
            self.api.transport.timeout = timeout
        self.engine.memory.transaction(lambda: self.engine.memory.event(
            self.engine.run_id,"campaign_llm_preflight",{
                "task":"generate_or_revise","model":self.model,
                "max_output_tokens":output_max,"prompt_sha256":digest(prompt),
                "prompt_chars":len(prompt),"per_request_timeout_seconds":timeout,
                "batch_max_wall_seconds":self.campaign_limits["max_wall_seconds"],
                "non_idempotent_tool_writes_enabled":False}))
        prior = self.engine.memory.db.execute(
            "SELECT count(*) FROM costs WHERE run_id=? AND category='llm'",
            (self.engine.run_id,)).fetchone()[0]
        started_request = time.monotonic()
        try:
            return super().complete(messages,tools)
        except BaseException as error:
            after = self.engine.memory.db.execute(
                "SELECT count(*) FROM costs WHERE run_id=? AND category='llm'",
                (self.engine.run_id,)).fetchone()[0]
            if after == prior:
                # Native ResearchAgent records ordinary ApiError including
                # ambiguous POST; this fallback covers custom provider
                # exceptions or cancellation after the request begins.
                self.engine.memory.cost(self.engine.run_id,"llm",
                    time.monotonic()-started_request,
                    input_tokens=None,output_tokens=None,usd=None,
                    detail={"outcome":"unknown_after_call_exception",
                            "error_type":type(error).__name__,
                            "vendor_token_usage":"unknown",
                            "vendor_charge":"unknown"})
            raise


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
        self.pending_reason = None

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
        if totals["unknown_token_calls"]:
            self.store.control(self.cid,"resource",
                "unreconciled_llm_token_usage_operator_review")
            return "paused_resource_unknown_token_usage"
        if (self.plan.get("max_total_known_usd") is not None
                and totals["unknown_usd_calls"]):
            self.store.control(self.cid,"resource",
                "unreconciled_llm_cost_operator_review")
            return "paused_resource_unknown_usd"
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

    def _verify_batch_source(self, ideas, cycle, provenance):
        """Mandatory source barrier before ANY Campaign LLM queue mutation.

        Campaign Live is still disabled at its public entry point. This guard
        also protects the internal registrar and Demo LLM campaigns, which
        must never claim fixture output as authenticated LLM proposals.
        """
        needs_llm = self.engine.config['mode'] == 'live' or self.plan['generator'] == 'llm'
        if not needs_llm:
            if provenance is not None:
                raise ResearchError('Demo fixture 批次不得冒用 LLM 来源证明')
            return None
        if self.plan['generator'] != 'llm':
            raise ResearchError('正式 Live Campaign 严禁 Demo 固定模板')
        if self.engine.config['mode'] == 'live':
            if (type(self.generator) is not CampaignDiscoveryAgent or
                    self.generator.engine is not self.engine or
                    self.generator.model not in {
                        self.engine.config['llm'].get('model'),
                        self.engine.config['llm'].get('fast_model'),
                        self.engine.config['llm'].get('reasoning_model')}):
                raise ResearchError('Live Campaign 必须使用绑定当前 Engine 的授权 DiscoveryAgent')
        if not isinstance(provenance, dict) or set(provenance) != {
                'batch_id', 'proposal_sha256', 'proposal_count', 'requested_model',
                'response_id', 'tool_call_id', 'source_kind'}:
            raise ResearchError('Campaign LLM 批次缺少完整来源证明，禁止入队')
        batch_id = provenance['batch_id']
        receipt = verify_source(self.engine.memory, self.engine.run_id, batch_id, ideas)
        # No caller is allowed to change an original response ID, model role,
        # proposal ordering or source label after the model reply was audited.
        for key in provenance:
            if provenance[key] != receipt.get(key):
                raise ResearchError('Campaign 提案关联收据不一致：' + key)
        if receipt['source_kind'] != 'authenticated_llm_tool_call':
            raise ResearchError('非 LLM 工具提案不能入队')
        started = [json.loads(r[0]) for r in self.engine.memory.db.execute(
            "SELECT payload FROM events WHERE run_id=? AND kind='llm_batch_started'",
            (self.engine.run_id,)) if json.loads(r[0]).get('batch_id') == batch_id]
        if len(started) != 1 or started[0].get('cycle') != cycle:
            raise ResearchError('Campaign LLM 提案来源周期与入队周期不同')
        prior = [json.loads(row[0]) for row in self.engine.memory.db.execute(
            "SELECT payload FROM events WHERE kind='campaign_proposal_source'")
            if json.loads(row[0]).get('campaign_id') == self.cid and
            json.loads(row[0]).get('batch_id') == batch_id]
        if prior:
            raise ResearchError('同一 LLM 工具响应批次已经登记，不允许重放')
        return receipt

    def _require_candidate_source(self, item):
        """Recheck persistent proof across Runs immediately before simulation."""
        if self.engine.config['mode'] != 'live' and self.plan['generator'] != 'llm':
            return
        events = []
        for row in self.engine.memory.db.execute(
                "SELECT run_id,payload FROM events WHERE kind='campaign_proposal_source'"):
            body = json.loads(row['payload'])
            if body.get('campaign_id') == self.cid and body.get('idea_id') == item['id']:
                events.append((row['run_id'], body))
        if len(events) != 1:
            raise ResearchError('待实验候选缺少唯一且持久的 LLM 来源绑定')
        source_run, proof = events[0]
        if (proof.get('proposal_sha256') != digest(item['idea']) or
                not self.engine.memory.db.execute(
                    'SELECT 1 FROM campaign_runs WHERE campaign_id=? AND run_id=?',
                    (self.cid, source_run)).fetchone()):
            raise ResearchError('候选内容/来源 Run 与持久审计不一致')
        # All original responses, including invalid/rejected/duplicate attempts,
        # are hash-anchored separately from mutable queue state. Never invent a
        # missing proposal when a batch only partly survived a process crash.
        attempts = []
        for row in self.engine.memory.db.execute(
                "SELECT run_id,payload FROM events WHERE kind='campaign_proposal_attempt'"):
            event = json.loads(row['payload'])
            if (row['run_id'] == source_run and event.get('campaign_id') == self.cid
                    and event.get('batch_id') == proof['batch_id']):
                attempts.append(event)
        attempts.sort(key=lambda event: event.get('proposal_index', -1))
        if [event.get('proposal_index') for event in attempts] != list(range(len(attempts))):
            raise ResearchError('模型批次缺少已入库/重复/拒绝的逐条提案哈希')
        origin = attempts[proof['proposal_index']]
        if (origin.get('idea_id') != item['id'] or
                origin.get('proposal_sha256') != proof.get('proposal_sha256')):
            raise ResearchError('Campaign 候选来源与初始提案序号不匹配')
        receipt = verify_source_hashes(
            self.engine.memory, source_run, proof['batch_id'],
            [event['proposal_sha256'] for event in attempts])
        if any(proof.get(local) != receipt.get(remote) for local, remote in (
                ('model', 'requested_model'), ('response_id', 'response_id'),
                ('tool_call_id', 'tool_call_id'))):
            raise ResearchError('候选模型身份与原始 LLM 工具响应不匹配')
        return receipt

    def _register_batch(self, ideas, cycle, *, original_ids=None, provenance=None):
        if not isinstance(ideas, list) or len(ideas) > self.plan["ideas_per_cycle"]:
            raise ResearchError("生成批次超出冻结上限")
        # The source proof must be complete BEFORE any queue insertion, even
        # when invoked directly, or when the Campaign is resumed from a prior Run.
        self._verify_batch_source(ideas, cycle, provenance)
        parents = self._parents()
        accepted = 0
        for index, idea in enumerate(ideas):
            original = original_ids[index] if original_ids else "%d:%d" % (cycle, index)
            try:
                valid = validate_idea(self.engine, idea, self.plan["domains"], parents)
                failure = None
            except ResearchError as error:
                valid, failure = None, str(error)
            ident, created = self.store.add(self.cid, idea, cycle, validated=valid,
                                            reason=failure, original_id=original)
            if valid is not None and self.engine.config['mode'] == 'live':
                units = validate_units(idea['expression'], self.engine.config['catalog_data'])
                self._record('unit_preflight', {
                    'idea_id': ident, 'unit_evidence_status': units['unit_status'],
                    'warning_codes': sorted({v['code'] for v in units['warnings']})})
            if self.plan['generator'] == 'llm':
                self._record('proposal_attempt', {
                    'idea_id': ident, 'cycle': cycle, 'proposal_index': index,
                    'proposal_sha256': digest(idea), 'batch_id': provenance['batch_id'],
                    'new_queue_record': created,
                    'source_kind': 'authenticated_llm_tool_call'})
                if created:
                    self._record('proposal_source', {
                        'idea_id': ident, 'cycle': cycle, 'proposal_index': index,
                        'proposal_sha256': digest(idea), 'batch_id': provenance['batch_id'],
                        'response_id': provenance['response_id'],
                        'tool_call_id': provenance['tool_call_id'],
                        'model': provenance['requested_model'],
                        'source_kind': 'authenticated_llm_tool_call'})
            if not created or valid is None:
                continue
            equivalent = find_equivalent(self.engine.memory, self.engine.context,
                idea['expression'], valid.fingerprint, additional=[
                    {'id': row['id'], 'expression': row['idea'].get('expression'),
                     'fingerprint': row['fingerprint']}
                    for row in self.store.rows(self.cid) if row.get('fingerprint')
                    and row['id'] != ident])
            if equivalent:
                self.store.move(ident, 'deferred', 'l1',
                    'normalized_formula_equivalent_requires_review')
                continue
            match = self.store.external_duplicate(
                self.plan["external_libraries"], self.engine.context, valid.fingerprint,
                self.plan["external_library_sha256"])
            if match:
                self.store.move(ident, "deferred", "l1",
                                "external_library_duplicate_requires_review:" +
                                match["library_sha256"] + ":" + match["trial_id"])
            else:
                self.store.move(ident, "queued", "l1", "L1_AST_and_economic_hypothesis_validated")
                accepted += 1
        return accepted

    def _generate(self):
        if self.generator is None:
            if self.plan["generator"] == "demo":
                raise ResearchError('旧 Campaign Demo 模板入口已下线；仅允许测试工具显式注入 fixtures')
            self.generator = CampaignDiscoveryAgent(
                self.engine,self.plan,self.limits,self.started)
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
            source = (self.generator.last_batch_provenance
                      if isinstance(self.generator, DiscoveryAgent) else None)
            count = self._register_batch(batch, cycle, provenance=source)
        except Exception:
            self.store.end_generation(self.cid, cycle, "failed", 0)
            # Do not retry a non-idempotent API request automatically.
            raise
        self.store.end_generation(self.cid, cycle, "completed", count)
        if not count:
            self.pending_reason = "search_stagnated_no_new_valid_candidate"
            self._record("stagnation", {
                "cycle": cycle, "generated_attempts": len(batch),
                "new_accepted_candidates": 0,
                "requires_feedback_revision_or_operator_review": True})
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
        # Reject unaudited restored candidates before changing status, writing
        # trial records or claiming a Simulation (not just on initial enqueue).
        self._require_candidate_source(item)
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
        """Old depth generator retired: a future LLM Worker must author trials.

        A historical study remains auditable. No deterministic lookback
        perturbations are permitted to create a new Alpha in v0.8.
        """
        self.store.move(item['id'], 'deferred', 'l2',
            'requires_new_llm_authored_robustness_hypothesis')
        self._record('depth_retired', {'idea_id':item['id'],
             'reason':'no_fixed_parameter_variant_generator'})

    def _recover_stale(self):
        # Never resubmit queued trials belonging to an old run, and never
        # silently replay a task that may have performed a remote write.
        stuck = self.store.rows(self.cid, state="testing")
        for item in stuck:
            self._require_candidate_source(item)
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
        if untested:
            if self.limits["max_new_simulations"] >= 2:
                # Preserve at least ONE fresh L1 opportunity in every batch.
                if self.base_in_run < 1:
                    return None
            else:
                # With a one-simulation batch we cannot do L1+L2 together.
                # Allocate L2 periodically, but only after sufficiently many
                # distinct L1 trials have received a baseline.
                cumulative_depth = self.store.db.execute(
                    "SELECT count(*) FROM campaign_depth WHERE campaign_id=? AND status='completed'",
                    (self.cid,)).fetchone()[0]
                if base_total < 3*(cumulative_depth+1):
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
        if self._available() and (
                self.base_in_run >= 1 or self.limits["max_new_simulations"] == 1):
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
            # Demo-only engineering completion is explicitly independent from
            # real Alpha performance quality. Synthetic metrics can satisfy
            # this fixture goal but NEVER quality_goal_count.
            goal = self.plan.get("demo_engineering_goal_count")
            if goal is not None:
                n = self.store.db.execute(
                    "SELECT count(*) FROM campaign_queue WHERE campaign_id=? "
                    "AND state='promising' AND phase='l3'", (self.cid,)
                ).fetchone()[0]
                if n >= goal:
                    self.store.control(self.cid, "complete", "demo_engineering_goal")
                    reason = "demo_engineering_goal"
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
                reason = self.pending_reason or "no_new_work_or_authorized_fields"
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

def _execute_campaign_locked(config, plan, limits=None, *, generator=None, simulator=None):
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
        except KeyboardInterrupt:
            if store.get(plan["campaign_id"])["status"] == "active":
                store.control(plan["campaign_id"], "pause", "operator_interrupt")
            engine.memory.transaction(lambda: store._log(
                plan["campaign_id"], "interrupt",
                {"run_id": engine.run_id, "no_automatic_retry": True}, engine.run_id))
            raise
        except Exception as error:
            if store.get(plan["campaign_id"])["status"] == "active":
                store.control(plan["campaign_id"], "fault",
                              "batch_error_requires_review:" + type(error).__name__)
            engine.memory.transaction(lambda: store._log(
                plan["campaign_id"], "fault_detail",
                {"error_type": type(error).__name__, "run_id": engine.run_id,
                 "non_idempotent_writes_retried": False}, engine.run_id))
            raise
    finally:
        engine.memory.close()

def execute_campaign(config, plan, limits=None, *, generator=None, simulator=None):
    """Public entry point owns the same per-database worker lock as legacy CLI.

    This prevents two independent Python processes from racing between
    candidate selection, POST reservation and campaign run creation.
    """
    from .cli import worker_lock
    with worker_lock(config["database"]):
        return _execute_campaign_locked(config, plan, limits,
                                        generator=generator, simulator=simulator)

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
