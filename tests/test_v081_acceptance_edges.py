"""v0.8.1 P1/P2 release acceptance: fail-closed budgets and model identities.

Provider requests are patched at the transport. No BRAIN or paid LLM calls.
Tests exercise the actual Agent, LLMRoles and research memory code paths.
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alpharesearch.common import ResearchError, load_settings
from alpharesearch.llm_provider_policy import _model_set, validate_llm_endpoint, PrivateLLMTransport
from alpharesearch.v08_pricing import reviewed_model_price, preflight_llm_budget
from alpharesearch.autoresearch_v08 import LLMRoles, Playbook, make_plan
from alpharesearch.engine import Engine
from alpharesearch.http import Response

ROOT=Path(__file__).resolve().parents[1]


def approved_price(*,input_rate=1.0,output_rate=3.0,zero_reviewed=False):
    return {
        "input_per_million_usd":input_rate,
        "output_per_million_usd":output_rate,
        "price_review":{
            "source":"Offline provider price schedule fixture 2026",
            "applicable_models":["test-model-v08"],
            "reviewed_at":(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),
            "reviewed_by":"Security price reviewer",
            "approval_ref":"FINANCE-PRICING-1234",
            "zero_price_reviewed":zero_reviewed,
        },
    }


class PricePolicyTests(unittest.TestCase):
    def setUp(self):
        self.config={"cost":approved_price(),
                     "llm":{"model":"test-model-v08","extra_params":{"max_tokens":4096}}}

    def test_missing_prices_are_unknown_not_free(self):
        self.config["cost"]={}
        value=reviewed_model_price(self.config,"test-model-v08",required=False)
        self.assertEqual(value["status"],"unknown_price")
        self.assertIsNone(value["input_per_million_usd"])
        with self.assertRaises(ResearchError):
            reviewed_model_price(self.config,"test-model-v08")

    def test_unreviewed_zero_price_is_rejected(self):
        self.config["cost"]=approved_price(input_rate=0.0,output_rate=0.0)
        with self.assertRaisesRegex(ResearchError,"零单价"):
            reviewed_model_price(self.config,"test-model-v08")

    def test_reviewed_zero_and_mixed_zero_are_distinct(self):
        self.config["cost"]=approved_price(input_rate=0.0,output_rate=0.0,
                                            zero_reviewed=True)
        value=reviewed_model_price(self.config,"test-model-v08")
        self.assertEqual(value["status"],"reviewed_zero")
        self.assertEqual(value["source"],"Offline provider price schedule fixture 2026")
        self.assertTrue(value["reviewed_at"].endswith("+00:00"))
        self.config["cost"]["output_per_million_usd"]=5.0
        mixed=reviewed_model_price(self.config,"test-model-v08")
        self.assertEqual(mixed["status"],"reviewed_mixed_zero")

    def test_regular_nonzero_price_is_reviewed_and_model_bound(self):
        value=reviewed_model_price(self.config,"test-model-v08")
        self.assertEqual(value["status"],"reviewed_nonzero")
        self.assertEqual(value["approval_ref"],"FINANCE-PRICING-1234")
        self.assertEqual(len(value["price_policy_sha256"]),64)
        with self.assertRaises(ResearchError):
            reviewed_model_price(self.config,"different-model")

    def test_price_source_and_review_date_mandatory_fresh(self):
        tests=[
            {"source":"TODO"},
            {"reviewed_at":"2020-01-01T00:00:00+00:00"},
            {"reviewed_at":"2026-10-25T00:00:00"},
            {"applicable_models":["some-other-model"]},
        ]
        for update in tests:
            with self.subTest(update=update):
                config=copy.deepcopy(self.config)
                config["cost"]["price_review"].update(update)
                with self.assertRaises(ResearchError):
                    reviewed_model_price(config,"test-model-v08")

    def test_dynamic_output_limit_shrinks_under_soft_usd_budget(self):
        self.config["cost"]=approved_price(input_rate=1.0,output_rate=5000.0)
        price=reviewed_model_price(self.config,"test-model-v08")
        value=preflight_llm_budget(messages=[{"role":"user","content":"new research question"}],
            tool_schema=[{"type":"function","function":{"name":"v08_test"}}],
            price=price,config=self.config,remaining_tokens=10000,remaining_usd=1.5)
        self.assertLess(value["max_output_tokens"],4096)
        self.assertGreaterEqual(value["max_output_tokens"],128)
        self.assertFalse(value["provider_invoice_hard_capped"])
        self.assertTrue(value["local_call_and_token_preflight_hard"])

    def test_insufficient_tokens_and_spend_fail_before_request(self):
        price=reviewed_model_price(self.config,"test-model-v08")
        for token_limit,usd in [(50,1.0),(10000,0.0000001)]:
            with self.subTest(tokens=token_limit,usd=usd):
                with self.assertRaises(ResearchError):
                    preflight_llm_budget(messages=[{"role":"user","content":"question"}],
                        tool_schema=[],price=price,config=self.config,
                        remaining_tokens=token_limit,remaining_usd=usd)

    def test_invalid_nan_inf_bool_and_negative_prices(self):
        for rate in (float("nan"),float("inf"),True,-0.01):
            with self.subTest(rate=rate):
                config=copy.deepcopy(self.config)
                config["cost"]["input_per_million_usd"]=rate
                with self.assertRaises(ResearchError):
                    reviewed_model_price(config,"test-model-v08")


class ModelIdentityConfigTests(unittest.TestCase):
    def test_unhashable_model_fields_raise_research_error(self):
        for key in ("model","fast_model","reasoning_model"):
            for value in ([],{},["model-a"],{"unsafe":"model-a"}):
                config={"model":"model-a","base_url":"http://localhost:1234/v1"}
                config[key]=value
                with self.subTest(field=key,value=value):
                    with self.assertRaises(ResearchError):
                        validate_llm_endpoint(config)

    def test_empty_whitespace_and_malformed_model_ids_rejected(self):
        for value in ("", " ", " model-a","model-a ","model\nnewline"):
            with self.subTest(value=value):
                with self.assertRaises(ResearchError):
                    _model_set({"model":value})

    def test_invalid_model_aliases_rejected_in_preflight(self):
        for aliases in (
            ["not-a-dict"],
            {"unapproved": []},
            {"model-a": [{}]},
            {"model-a": [{"returned_model":[],"reviewed_by":"A",
                          "approval_ref":"R","reason":"A very meaningful review"}]},
            {"model-a": [{"returned_model":"model-b","reviewed_by":" ",
                          "approval_ref":"R","reason":"A very meaningful review"}]},
        ):
            with self.subTest(aliases=aliases):
                with self.assertRaises(ResearchError):
                    _model_set({"model":"model-a","response_model_aliases":aliases})

    def test_valid_reviewed_alias_remains_allowed(self):
        config={"model":"model-a","response_model_aliases":{
            "model-a":[{"returned_model":"vendor-model-a",
                        "reviewed_by":"Security reviewer",
                        "approval_ref":"ALIAS-REVIEW-123",
                        "reason":"Provider identifies the identical deployed weights by an alias"}]}}
        self.assertEqual(_model_set(config),["model-a"])


class RealRoleCostBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conf=load_settings(ROOT/"config/settings.toml")
        self.conf["database"]=str(Path(tmp.name)/"fresh.sqlite3")
        self.conf["mode"]="demo"
        self.conf["llm"]={
            "model":"test-model-v08",
            "base_url":"http://localhost:11434/v1",
            "timeout_seconds":5,
            "extra_params":{"max_tokens":4096},
        }
        self.conf["cost"]=approved_price()
        self.plan=make_plan(self.conf,project_id="v081-price-safety",
                            objective="Only synthetic boundary tests",
                            max_llm_calls_per_batch=6,max_known_usd_per_batch=1.0)
        self.engine=Engine(self.conf,simulation_limit=0)
        self.addCleanup(self.engine.memory.close)
        self.book=Playbook(self.engine.memory)
        self.book.register(self.plan)

    def _ask(self,side_effect=None,response=None):
        with patch.dict(os.environ,{"LLM_API_KEY":"fake-offline-only"}):
            roles=LLMRoles(self.engine,self.book,self.plan)
            with patch.object(PrivateLLMTransport,"request",
                              side_effect=side_effect,return_value=response) as request:
                if side_effect:
                    with self.assertRaises(Exception):
                        roles.ask("explorer",{"goal":"test"},start_time=__import__("time").monotonic())
                else:
                    answer=roles.ask("explorer",{"goal":"test"},start_time=__import__("time").monotonic())
                    return answer,request.call_args,request.call_count
                return request.call_count

    def test_real_role_rejects_unreviewed_zero_without_model_call(self):
        self.conf["cost"]=approved_price(input_rate=0.0,output_rate=0.0)
        with patch.dict(os.environ,{"LLM_API_KEY":"fake-offline-only"}):
            roles=LLMRoles(self.engine,self.book,self.plan)
            with patch.object(PrivateLLMTransport,"request") as request:
                with self.assertRaises(ResearchError):
                    roles.ask("explorer",{"goal":"test"},start_time=__import__("time").monotonic())
                self.assertEqual(request.call_count,0)
        self.assertEqual(self.engine.memory.db.execute(
            "SELECT COUNT(*) FROM v08_requests").fetchone()[0],0)

    def test_real_role_max_tokens_and_price_origin_are_audited(self):
        self.conf["cost"]=approved_price(input_rate=1.0,output_rate=500.0)
        response=Response(200,{},data={
            "model":"test-model-v08","id":"provider-123",
            "usage":{"prompt_tokens":35,"completion_tokens":30},
            "choices":[{"message":{"tool_calls":[{
                "id":"tool-123","function":{"name":"v08_explorer",
                  "arguments":json.dumps({"hypotheses":[]})}}]}}]})
        (_reply,request_id),call,n=self._ask(response=response)
        self.assertEqual(n,1)
        payload=call.args[2]
        self.assertLess(payload["max_tokens"],4096)
        self.assertGreaterEqual(payload["max_tokens"],128)
        cost=self.engine.memory.db.execute(
            "SELECT input_tokens,output_tokens,usd,detail FROM costs WHERE category='llm'").fetchone()
        self.assertEqual((cost["input_tokens"],cost["output_tokens"]),(35,30))
        self.assertGreater(cost["usd"],0)
        detail=json.loads(cost["detail"])
        self.assertEqual(detail["price_status"],"reviewed_nonzero")
        self.assertEqual(detail["price_source"],"Offline provider price schedule fixture 2026")
        pre=self.engine.memory.db.execute(
            "SELECT payload FROM events WHERE kind='v08_llm_budget_preflight'").fetchone()
        self.assertFalse(json.loads(pre[0])["provider_invoice_hard_capped"])

    def test_successful_response_missing_usage_remains_unknown_not_free(self):
        response=Response(200,{},data={
            "model":"test-model-v08","id":"provider-unknown",
            "choices":[{"message":{"tool_calls":[{
                "id":"tool-unknown","function":{"name":"v08_explorer",
                 "arguments":json.dumps({"hypotheses":[]})}}]}}]})
        with patch.dict(os.environ,{"LLM_API_KEY":"fake-offline-only"}):
            roles=LLMRoles(self.engine,self.book,self.plan)
            with patch.object(PrivateLLMTransport,"request",return_value=response):
                with self.assertRaisesRegex(ResearchError,"用量未知"):
                    roles.ask("explorer",{"goal":"test"},start_time=__import__("time").monotonic())
        cost=self.engine.memory.db.execute(
            "SELECT input_tokens,output_tokens,usd FROM costs WHERE category='llm'").fetchone()
        self.assertEqual(tuple(cost),(None,None,None))
        self.assertEqual(self.engine.memory.db.execute(
            "SELECT state FROM v08_requests").fetchone()[0],"failed")
        self.assertTrue(json.loads(self.engine.memory.db.execute(
            "SELECT payload FROM events WHERE kind='v08_llm_budget_observed'").fetchone()[0])[
            "unknown_usage_or_cost"])


if __name__=="__main__":
    unittest.main()
