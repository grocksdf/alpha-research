"""v0.8.2 LLM-output hardening / formula repair / zero-candidate safety.

All models are explicitly test-scripted or transport-mocked; none can be
mistaken for genuine paid LLM success or BRAIN market evidence.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import types
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch

from alpharesearch.common import ResearchError,load_settings
from alpharesearch.autoresearch_v08 import make_plan,execute_batch,Playbook,LLMRoles
from alpharesearch.v082_protocol import (ModelOutputTruncated,ModelToolCallMissing,
                                          ModelToolCallInvalid,parse_tool_response)
from alpharesearch.v082_capabilities import model_capabilities,request_parameters
from alpharesearch.v08_pricing import reviewed_model_price,preflight_llm_budget
from alpharesearch.memory import Memory
from alpharesearch.engine import Engine
from alpharesearch.llm_provider_policy import PrivateLLMTransport
from alpharesearch.http import Response

from test_v08_autoresearch import CatalogDerivedScript

ROOT=Path(__file__).resolve().parents[1]
PHASES=("explorer","strategist","worker","critic","playbook")


def capability(model="offline-tested-model",role_limit=8192,output_parameter="max_completion_tokens",
               reasoning_parameter="reasoning_effort",reasoning_value="low"):
    return {"models":{model:{
        "model":model,"provider":"offline-model-protocol-fixture",
        "reviewed_by":"Offline QA reviewer",
        "review_ref":"CAPABILITY-TEST-123",
        "reviewed_at":(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),
        "capability_source":"Offline compatibility fixture; NOT provider confirmation",
        "max_context_tokens":32768,"max_output_tokens":16384,
        "output_token_parameter":output_parameter,
        "role_output_tokens":{role:role_limit for role in PHASES},
        "reasoning_parameter":reasoning_parameter,
        "reasoning_value":reasoning_value,
        "tool_choice_required_supported":True,
    }}}


class StructureTests(unittest.TestCase):
    def test_truncated_2048_with_no_tool_calls_is_specific_error(self):
        with self.assertRaises(ModelOutputTruncated):
            parse_tool_response({"choices":[{
                "finish_reason":"length","message":{"content":"not a tool call"}}]},
                "critic")

    def test_missing_choices_message_calls_and_finished_text_never_count_as_tool(self):
        for reply in (None,{},{"choices":[]},
                      {"choices":[{"finish_reason":"stop","message":{}}]},
                      {"choices":[{"finish_reason":"stop","message":{
                          "content":"an Alpha suggestion in plain text"}}]},
                      {"choices":[{"finish_reason":None,"message":{"tool_calls":[]}}]}):
            with self.subTest(reply=reply):
                with self.assertRaises(ModelToolCallMissing):
                    parse_tool_response(reply,"worker")

    def test_tools_are_strictly_named_and_structured(self):
        for calls in (
            [{"id":"1","function":{"name":"submit","arguments":"{}"}}],
            [{"id":"1","function":{"name":"v08_worker","arguments":"{"}}],
            [{"id":"1","function":{"name":"v08_worker","arguments":"[]"}}],
            [{"id":"1","function":{"name":"v08_worker","arguments":"{}"}},
             {"id":"2","function":{"name":"v08_worker","arguments":"{}"}}],
        ):
            with self.subTest(calls=calls):
                with self.assertRaises(ModelToolCallInvalid):
                    parse_tool_response({"choices":[{
                        "finish_reason":"tool_calls","message":{"tool_calls":calls}}]},"worker")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.conf={"llm":{"model":"offline-tested-model",
                          "v08_capabilities":capability()},
                   "cost":{
                       "input_per_million_usd":1.0,
                       "output_per_million_usd":5.0,
                       "price_review":{
                           "source":"TEST ONLY reviewed cost source",
                           "applicable_models":["offline-tested-model"],
                           "reviewed_at":(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),
                           "reviewed_by":"QA reviewer",
                           "approval_ref":"PRICE-FIXTURE-123",
                           "zero_price_reviewed":False,
                       }}}

    def test_role_capability_allows_4096_and_8192_without_2048_ceiling(self):
        for role_cap in (4096,8192):
            with self.subTest(cap=role_cap):
                self.conf["llm"]["v08_capabilities"]=capability(role_limit=role_cap)
                profile=model_capabilities(self.conf,"offline-tested-model")
                price=reviewed_model_price(self.conf,"offline-tested-model")
                budget=preflight_llm_budget(
                    phase="critic",capability=profile,
                    messages=[{"role":"user","content":"review the proposal"}],
                    tool_schema=[{"type":"function","function":{"name":"v08_critic"}}],
                    price=price,config=self.conf,
                    remaining_tokens=30000,remaining_usd=100.0)
                self.assertEqual(budget["max_output_tokens"],role_cap)
                self.assertEqual(budget["output_token_parameter"],"max_completion_tokens")
                args=request_parameters(profile,"critic",budget["max_output_tokens"])
                self.assertEqual(args["max_completion_tokens"],role_cap)
                self.assertNotIn("max_tokens",args)
                self.assertEqual(args["reasoning_effort"],"low")

    def test_unsupported_thinking_flags_fail_closed_before_request(self):
        conf=copy.deepcopy(self.conf)
        profile=conf["llm"]["v08_capabilities"]["models"]["offline-tested-model"]
        profile["reasoning_value"]="please-disable-thinking"
        with self.assertRaises(ResearchError):
            model_capabilities(conf,"offline-tested-model")
        profile["reasoning_value"]="low"
        profile["reasoning_parameter"]="thinking"
        profile["reasoning_value"]=None
        with self.assertRaises(ResearchError):
            model_capabilities(conf,"offline-tested-model")

    def test_official_moonshot_k3_cannot_inherit_k2_thinking_disable(self):
        conf=copy.deepcopy(self.conf)
        conf["llm"]["model"]="kimi-k3"
        profile=capability(model="kimi-k3")["models"]["kimi-k3"]
        profile["provider"]="moonshot_direct_kimi_api"
        profile["reasoning_parameter"]="thinking"
        profile["reasoning_value"]={"type":"disabled"}
        conf["llm"]["v08_capabilities"]={"models":{"kimi-k3":profile}}
        with self.assertRaises(ResearchError):
            model_capabilities(conf,"kimi-k3")
        profile["reasoning_parameter"]="reasoning_effort"
        profile["reasoning_value"]="low"
        self.assertEqual(model_capabilities(conf,"kimi-k3")["reasoning_value"],"low")

    def test_reviewed_parameter_may_be_max_tokens_instead(self):
        conf=copy.deepcopy(self.conf)
        conf["llm"]["v08_capabilities"]=capability(output_parameter="max_tokens",
                                                   reasoning_parameter="none",reasoning_value=None)
        profile=model_capabilities(conf,"offline-tested-model")
        self.assertEqual(request_parameters(profile,"worker",4096),{"max_tokens":4096})

    def test_provider_probe_is_network_inert_without_explicit_execution(self):
        from verification.v082_model_probe import probe
        conf=copy.deepcopy(self.conf)
        conf["llm"].update({
            "base_url":"http://localhost:11434/v1",
            "timeout_seconds":5})
        conf["mode"]="demo"
        conf["safety"]={"live_research_enabled":False,"submission_enabled":False}
        with patch.dict(os.environ,{"LLM_API_KEY":""}):
            with patch.object(PrivateLLMTransport,"request") as connection:
                result=probe(conf,"offline-tested-model",[4096,8192],5.0,
                             execute=False)
                self.assertEqual(connection.call_count,0)
                self.assertEqual(result["real_llm_requests"],0)
                self.assertEqual(result["status"],"BLOCKED_NO_EXECUTE_FLAG_OR_AUTHORIZED_CREDENTIAL")

    def test_finite_profile_guard_does_not_accept_unbounded_config(self):
        conf=copy.deepcopy(self.conf)
        prof=conf["llm"]["v08_capabilities"]["models"]["offline-tested-model"]
        prof["max_output_tokens"]=10**9
        prof["role_output_tokens"]["worker"]=10**9
        with self.assertRaises(ResearchError):
            model_capabilities(conf,"offline-tested-model")

    def test_role_output_does_not_exceed_plan_or_cost_budget(self):
        cap=model_capabilities(self.conf,"offline-tested-model")
        price=reviewed_model_price(self.conf,"offline-tested-model")
        with self.assertRaises(ResearchError):
            preflight_llm_budget(
                phase="worker",capability=cap,messages=[{"role":"user","content":"brief"}],
                tool_schema=[],price=price,config=self.conf,
                remaining_tokens=1200,remaining_usd=0.000001)


class _Base(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conf=load_settings(ROOT/"config/settings.toml")
        self.conf["database"]=str(Path(tmp.name)/"v082-new.sqlite3")
        self.conf["llm"]["model"]="offline-only-model"
        self.conf["mode"]="demo"
        self.plan=make_plan(self.conf,project_id="v082-test",
                            objective="Investigate authorized feature relationships")

    def store(self):
        m=Memory(self.conf["database"])
        return m,Playbook(m)


class WorkerAndCriticTests(_Base):
    def test_unsupported_multiply_operator_is_llm_repaired_before_critic(self):
        class RepairMultiply(CatalogDerivedScript):
            def __init__(self):
                super().__init__()
                self.invalid_once=False
                self.repair_context=None
            def complete(self,phase,messages,schema,model):
                context=json.loads(messages[-1]["content"])
                result=super().complete(phase,messages,schema,model)
                if phase=="worker":
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    if "repair_feedback" in context:
                        self.repair_context=context["repair_feedback"]
                        body["proposals"][0]["difference_from_parent"]="Corrected invalid original operator using validated DSL"
                    elif not self.invalid_once:
                        self.invalid_once=True
                        body["proposals"][0]["expression"]="multiply(close,volume)"
                    fn["arguments"]=json.dumps(body)
                return result
        model=RepairMultiply()
        result=execute_batch(self.conf,self.plan,adapter=model)
        self.assertEqual(result["new_trials"],1)
        self.assertEqual(result["new_candidates"],1)
        self.assertEqual(result["invalid_formula_attempts"],1)
        self.assertEqual(model.repair_context["deterministic_error"]["illegal_operator"],"multiply")
        m,book=self.store()
        try:
            records=[json.loads(r[0]) for r in m.db.execute(
                "SELECT payload FROM events WHERE kind='v08_formula_validation_attempt' ORDER BY id")]
            self.assertEqual(len(records),2)
            self.assertEqual([r["validation_status"] for r in records],["invalid","valid"])
            self.assertEqual(records[1]["parent_attempt_sha256"],records[0]["attempt_sha256"])
            self.assertNotEqual(records[1]["source_worker_request_id"],records[0]["source_worker_request_id"])
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_knowledge").fetchone()[0],1)
        finally:m.close()

    def test_valid_sibling_simulates_when_other_formula_is_invalid(self):
        class Mixed(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                result=super().complete(phase,messages,schema,model)
                if phase=="worker":
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    bad={**body["proposals"][0],
                         "expression":"negative(close)",
                         "difference_from_parent":"Invalid vendor expression (deliberate test input)"}
                    body["proposals"].append(bad)
                    fn["arguments"]=json.dumps(body)
                if phase=="critic":
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["reviews"]=body["reviews"][:1]
                    fn["arguments"]=json.dumps(body)
                return result
        self.plan=make_plan(self.conf,project_id="v082-test",
            objective=self.plan["objective"],max_llm_calls_per_batch=5)
        result=execute_batch(self.conf,self.plan,adapter=Mixed())
        self.assertEqual(result["new_trials"],1)
        self.assertEqual(result["new_candidates"],1)
        self.assertEqual(result["invalid_formula_attempts"],1)
        m,_=self.store()
        try:self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_queue").fetchone()[0],1)
        finally:m.close()

    def test_every_formula_invalid_causes_pause_without_fake_market_knowledge(self):
        class AlwaysBad(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                response=super().complete(phase,messages,schema,model)
                if phase=="worker":
                    fn=response.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    for item in body["proposals"]: item["expression"]="negative(close)"
                    fn["arguments"]=json.dumps(body)
                return response
        with self.assertRaises(ResearchError):
            execute_batch(self.conf,self.plan,adapter=AlwaysBad())
        m,store=self.store()
        try:
            self.assertEqual(store.status("v082-test")["status"],"paused_fault")
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_queue").fetchone()[0],0)
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM trials").fetchone()[0],0)
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_knowledge").fetchone()[0],0)
            self.assertGreaterEqual(m.db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='v08_formula_validation_attempt'").fetchone()[0],1)
        finally:m.close()

    def test_critic_defer_persists_revision_and_next_batch_approves(self):
        class Deferred(CatalogDerivedScript):
            def __init__(self):
                super().__init__()
                self.defer_once=True
            def complete(self,phase,messages,schema,model):
                context=json.loads(messages[-1]["content"])
                result=super().complete(phase,messages,schema,model)
                if phase=="explorer" and context["pending_critic_revisions"]:
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["hypotheses"][0]["parent_hypothesis_id"]=context[
                        "pending_critic_revisions"][0]["hypothesis_id"]
                    fn["arguments"]=json.dumps(body)
                if phase=="worker" and context["pending_critic_revisions"]:
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["proposals"][0]["parent_revision_id"]=context[
                        "pending_critic_revisions"][0]["revision_id"]
                    body["proposals"][0]["difference_from_parent"]="Revised the deferred economic mechanism after peer feedback"
                    fn["arguments"]=json.dumps(body)
                if phase=="critic" and self.defer_once:
                    self.defer_once=False
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["reviews"][0]["verdict"]="defer"
                    body["reviews"][0]["rationale"]="Need an independent explanation of the economic mechanism"
                    fn["arguments"]=json.dumps(body)
                return result
        model=Deferred()
        first=execute_batch(self.conf,self.plan,adapter=model)
        self.assertEqual(first["new_trials"],0)
        self.assertEqual(first["critic_deferrals"],1)
        self.assertEqual(first["knowledge_revisions"],[])
        second=execute_batch(self.conf,self.plan,adapter=model)
        self.assertEqual(second["new_trials"],1)
        m,store=self.store()
        try:
            r=m.db.execute("SELECT state,depth,parent_revision_id FROM v08_revisions").fetchall()
            self.assertEqual(len(r),1)
            self.assertEqual(r[0]["state"],"resolved")
            evidence=[json.loads(row[0]) for row in m.db.execute(
                "SELECT payload FROM events WHERE kind='v08_critic_revision_resolved'")]
            self.assertEqual(len(evidence),1)
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_knowledge").fetchone()[0],1)
            self.assertTrue(store.audit("v082-test")["event_chain_verified"])
            m.db.execute("UPDATE v08_revisions SET state='pending'")
            with self.assertRaises(ResearchError):
                store.audit("v082-test")
        finally:m.close()

    def test_two_empty_defer_batches_pause_instead_of_infinite_run(self):
        class AlwaysDefer(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                context=json.loads(messages[-1]["content"])
                result=super().complete(phase,messages,schema,model)
                if phase=="explorer" and context["pending_critic_revisions"]:
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["hypotheses"][0]["parent_hypothesis_id"]=context[
                        "pending_critic_revisions"][0]["hypothesis_id"]
                    fn["arguments"]=json.dumps(body)
                if phase=="worker" and context["pending_critic_revisions"]:
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["proposals"][0]["parent_revision_id"]=context[
                        "pending_critic_revisions"][0]["revision_id"]
                    fn["arguments"]=json.dumps(body)
                if phase=="critic":
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    body=json.loads(fn["arguments"])
                    body["reviews"][0]["verdict"]="defer"
                    body["reviews"][0]["rationale"]="Further economic justification is required before a test"
                    fn["arguments"]=json.dumps(body)
                return result
        model=AlwaysDefer()
        first=execute_batch(self.conf,self.plan,adapter=model)
        self.assertEqual(first["status"],"active")
        with self.assertRaises(ResearchError):
            execute_batch(self.conf,self.plan,adapter=model)
        m,store=self.store()
        try:
            self.assertEqual(store.status("v082-test")["status"],"paused_fault")
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM trials").fetchone()[0],0)
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_knowledge").fetchone()[0],0)
            self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_revisions").fetchone()[0],2)
        finally:m.close()

    def test_campaign_llm_call_budget_does_not_reset_on_new_batch(self):
        script=CatalogDerivedScript()
        self.plan=make_plan(self.conf,project_id="v082-test",
            objective=self.plan["objective"],
            max_campaign_llm_calls=6)
        first=execute_batch(self.conf,self.plan,adapter=script)
        self.assertEqual(first["new_trials"],1)
        with self.assertRaises(ResearchError):
            execute_batch(self.conf,self.plan,adapter=script)
        m,book=self.store()
        try:
            self.assertEqual(book.status("v082-test")["status"],"paused_fault")
            self.assertEqual(m.db.execute(
                "SELECT COUNT(*) FROM v08_requests").fetchone()[0],6)
            self.assertEqual(m.db.execute(
                "SELECT COUNT(*) FROM trials").fetchone()[0],1)
            self.assertEqual(m.db.execute(
                "SELECT COUNT(*) FROM v08_knowledge").fetchone()[0],1)
        finally:m.close()

    def test_changes_to_frozen_model_capability_require_new_plan(self):
        config=copy.deepcopy(self.conf)
        config["llm"]["v08_capabilities"]=capability(model="offline-only-model")
        with self.assertRaisesRegex(ResearchError,"冻结"):
            execute_batch(config,self.plan,adapter=CatalogDerivedScript())


class RealTransportFaultTests(_Base):
    def _real_engine(self):
        self.conf["llm"]={
            "base_url":"http://localhost:11434/v1",
            "model":"offline-tested-model",
            "timeout_seconds":5,
            "v08_capabilities":capability(),
        }
        self.conf["cost"]={
            "input_per_million_usd":2.0,
            "output_per_million_usd":8.0,
            "price_review":{
                "source":"Official test-only pricing review receipt",
                "applicable_models":["offline-tested-model"],
                "reviewed_at":(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),
                "reviewed_by":"Offline QA reviewer",
                "approval_ref":"OFFLINE-TEST-PRICING-123",
                "zero_price_reviewed":False}}
        plan=make_plan(self.conf,project_id="v082-test",
                       objective=self.plan["objective"],max_known_usd_per_batch=2.0)
        engine=Engine(self.conf,simulation_limit=0)
        book=Playbook(engine.memory)
        book.register(plan)
        return engine,book,plan

    def test_live_style_tool_truncation_is_charged_and_diagnosed(self):
        engine,book,plan=self._real_engine()
        self.addCleanup(engine.memory.close)
        with patch.dict(os.environ,{"LLM_API_KEY":"offline-test-key"}):
            roles=LLMRoles(engine,book,plan)
            response=Response(200,{},data={
                "id":"provider-response-2048","model":"offline-tested-model",
                "usage":{"prompt_tokens":320,"completion_tokens":2048,
                         "completion_tokens_details":{"reasoning_tokens":1900}},
                "choices":[{"finish_reason":"length","message":{"content":"truncated"}}]})
            with patch.object(PrivateLLMTransport,"request",return_value=response) as request:
                with self.assertRaises(ModelOutputTruncated):
                    roles.ask("critic",{"goal":"test"},start_time=time.monotonic())
                self.assertEqual(request.call_count,1)
                body=request.call_args.args[2]
                self.assertGreater(body["max_completion_tokens"],2048)
                self.assertNotIn("max_tokens",body)
        diag=[json.loads(x[0]) for x in engine.memory.db.execute(
            "SELECT payload FROM events WHERE kind='v08_llm_output_diagnostic'")]
        self.assertEqual(len(diag),1)
        self.assertEqual(diag[0]["finish_reason"],"length")
        self.assertEqual(diag[0]["completion_tokens"],2048)
        self.assertEqual(diag[0]["reasoning_tokens"],1900)
        self.assertEqual(diag[0]["role"],"critic")
        self.assertNotIn("truncated",json.dumps(diag))
        self.assertEqual(engine.memory.db.execute(
            "SELECT input_tokens,output_tokens FROM costs WHERE category='llm'").fetchone()[1],2048)
        self.assertEqual(engine.memory.db.execute("SELECT state FROM v08_requests").fetchone()[0],"failed")

    def test_missing_tool_call_has_specific_error_and_no_retry(self):
        engine,book,plan=self._real_engine()
        self.addCleanup(engine.memory.close)
        with patch.dict(os.environ,{"LLM_API_KEY":"offline-test-key"}):
            roles=LLMRoles(engine,book,plan)
            response=Response(200,{},data={
                "id":"provider-response-no-tools","model":"offline-tested-model",
                "usage":{"prompt_tokens":500,"completion_tokens":100},
                "choices":[{"finish_reason":"stop","message":{"content":"plain"} }]})
            with patch.object(PrivateLLMTransport,"request",return_value=response) as request:
                with self.assertRaises(ModelToolCallMissing):
                    roles.ask("critic",{"goal":"test"},start_time=time.monotonic())
                self.assertEqual(request.call_count,1)
        events=engine.memory.db.execute(
            "SELECT COUNT(*) FROM events WHERE kind='v08_llm_output_diagnostic'").fetchone()[0]
        self.assertEqual(events,1)

    def test_manual_retry_is_explicit_single_use_and_not_brain_replay(self):
        engine,book,plan=self._real_engine()
        self.addCleanup(engine.memory.close)
        response=Response(200,{},data={
            "id":"truncated-for-operator","model":"offline-tested-model",
            "usage":{"prompt_tokens":40,"completion_tokens":2048},
            "choices":[{"finish_reason":"length","message":{"content":"partial"}}]})
        with patch.dict(os.environ,{"LLM_API_KEY":"offline-test-key"}):
            roles=LLMRoles(engine,book,plan)
            with patch.object(PrivateLLMTransport,"request",return_value=response):
                with self.assertRaises(ModelOutputTruncated):
                    roles.ask("critic",{"goal":"test"},start_time=time.monotonic())
        failed=engine.memory.db.execute(
            "SELECT id,state FROM v08_requests").fetchone()
        self.assertEqual(failed["state"],"failed")
        book.control("v082-test","fault","truncated_tool_reply")
        with self.assertRaises(ResearchError):
            book.control("v082-test","resume","retry-without-approval")
        reason="approve-model-retry:"+failed["id"]+":OFFLINE-MANUAL-APPROVAL-123"
        self.assertEqual(book.control("v082-test","resume",reason),"active")
        receipt=engine.memory.db.execute(
            "SELECT payload FROM events WHERE kind='v08_model_retry_authorized'").fetchone()
        self.assertEqual(json.loads(receipt[0])["original_failed_request_id"],failed["id"])
        # A later retry authorization cannot silently reset the frozen limit.
        book.control("v082-test","fault","still_truncated")
        with self.assertRaises(ResearchError):
            book.control("v082-test","resume",reason)



if __name__=="__main__":
    unittest.main()
