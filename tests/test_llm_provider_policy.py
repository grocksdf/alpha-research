"""v0.8 provider egress must be operator approved and never contact BRAIN.

All tests mock or omit network I/O. Real-model discovery is NOT asserted.
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
from alpharesearch.llm_provider_policy import (
    V08LLMClient, authorized_v08_agent, validate_llm_endpoint,
)
from alpharesearch.memory import Memory
from alpharesearch.engine import Engine
from alpharesearch.autoresearch_v08 import make_plan, execute_batch, Playbook

ROOT = Path(__file__).resolve().parents[1]


def reviewed_llm():
    now = datetime.now(timezone.utc)
    fmt = lambda date: date.isoformat()
    return {
        "base_url": "https://api.openai.com/v1", "model": "reviewed-model-a",
        "timeout_seconds": 18,
        "remote_authorization": {
            "approved_base_url": "https://api.openai.com/v1",
            "approved_models": ["reviewed-model-a"],
            "approved_by": "Local security reviewer",
            "approval_ref": "SEC-REVIEW-2026-1234",
            "approved_at": fmt(now-timedelta(hours=1)),
            "expires_at": fmt(now+timedelta(days=12)),
            "data_export_approved": True,
            "approval_note": "Reviewed approved field names and synthetic-only research materials",
        },
    }


class LLMEndpointPolicyTests(unittest.TestCase):
    def test_loopback_http_is_allowed_without_remote_approval(self):
        for host in ("127.0.0.1", "[::1]", "localhost"):
            policy = validate_llm_endpoint({"base_url": "http://"+host+":11434/v1", "model": "local-test-model"})
            self.assertEqual(policy["kind"], "loopback")
            client = V08LLMClient({"base_url": "http://"+host+":11434/v1", "model": "local-test-model"})
            self.assertEqual(client.origin[0], "http")
            self.assertEqual(client.url(client.base+"chat/completions"), client.base+"chat/completions")

    def test_remote_cannot_be_accidentally_enabled_by_default_config(self):
        policy = reviewed_llm()
        with self.assertRaisesRegex(ResearchError, "allow-remote-llm"):
            validate_llm_endpoint(policy)
        not_approved = copy.deepcopy(policy)
        not_approved.pop("remote_authorization")
        with self.assertRaises(ResearchError):
            validate_llm_endpoint(not_approved,operator_confirmed=True)
        self.assertEqual(validate_llm_endpoint(policy,operator_confirmed=True)["kind"],"remote_approved_https")
        spoofed = copy.deepcopy(policy)
        spoofed["base_url"] = "https://api.openai.com.evil.example/v1"
        with self.assertRaises(ResearchError):
            validate_llm_endpoint(spoofed,operator_confirmed=True)

    def test_untrusted_url_and_proxy_payloads_are_rejected(self):
        invalid = (
            "http://api.openai.com/v1", "https://api.openai.com:8443/v1",
            "https://user:password@api.openai.com/v1", "https://api.openai.com/v1?token=bad",
            "https://192.168.1.5/v1",
            "http://169.254.169.254/latest/meta-data", "https://[::1]/v1?bad=1",
            "https://api.openai.com/v1/../secret", "https://api.openai.com/%2e%2e/v1",
            "https://api.openai.com/v1#fragment", "ftp://api.openai.com/v1",
        )
        for url in invalid:
            with self.subTest(url=url):
                policy = reviewed_llm()
                policy["base_url"] = url
                policy["remote_authorization"]["approved_base_url"] = url
                with self.assertRaises(ResearchError):
                    validate_llm_endpoint(policy,operator_confirmed=True)

    def test_exact_path_model_and_time_bound_approvals(self):
        ok = reviewed_llm()
        self.assertTrue(validate_llm_endpoint(ok,operator_confirmed=True)["policy_sha256"])
        cases=[]
        p=copy.deepcopy(ok);p["remote_authorization"]["approved_base_url"]="https://api.openai.com/v2";cases.append(p)
        p=copy.deepcopy(ok);p["model"]="different-unapproved-model";cases.append(p)
        p=copy.deepcopy(ok);p["fast_model"]="unreviewed-fast-model";cases.append(p)
        p=copy.deepcopy(ok);p["remote_authorization"]["data_export_approved"]=False;cases.append(p)
        p=copy.deepcopy(ok);p["remote_authorization"]["approval_ref"]="TODO";cases.append(p)
        p=copy.deepcopy(ok);p["remote_authorization"]["expires_at"]="2020-01-01T00:00:00+00:00";cases.append(p)
        p=copy.deepcopy(ok);p["remote_authorization"]["expires_at"]="2035-01-01T00:00:00+00:00";cases.append(p)
        p=copy.deepcopy(ok);p["remote_authorization"]["approved_by"]="REPLACE ME";cases.append(p)
        for policy in cases:
            with self.subTest(policy=policy):
                with self.assertRaises(ResearchError):
                    validate_llm_endpoint(policy,operator_confirmed=True)

    def test_remote_transport_cannot_redirect_to_another_host(self):
        client=V08LLMClient(reviewed_llm(),operator_confirmed=True)
        self.assertEqual(client.origin,("https","api.openai.com",None))
        with self.assertRaises(ResearchError):
            client.url("https://evil.example/v1/chat/completions")
        with self.assertRaises(ResearchError):
            client.url("http://api.openai.com/v1/chat/completions")
        # No network is attempted when constructing or validating the policy.
        self.assertIsNotNone(client.transport.opener)

    def test_real_provider_construction_with_no_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            conf=load_settings(ROOT/"config/settings.toml")
            conf["database"]=str(Path(folder)/"v08-isolated.sqlite3")
            conf["llm"]=reviewed_llm()
            conf["_v08_remote_operator_confirmed"]=True
            with patch.dict(os.environ,{"LLM_API_KEY":""}):
                plan=make_plan(conf,project_id="missing-key",objective="Audit failed provider preflight")
                with self.assertRaises(ResearchError):
                    execute_batch(conf,plan)
            m=Memory(conf["database"])
            try:
                store=Playbook(m)
                self.assertEqual(store.status("missing-key")["status"],"paused_fault")
                self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_queue").fetchone()[0],0)
            finally: m.close()

    def test_unapproved_remote_provider_blocks_direct_api_before_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            conf=load_settings(ROOT/"config/settings.toml")
            conf["database"]=str(Path(folder)/"v08-isolated.sqlite3")
            conf["llm"]=reviewed_llm()
            conf["_v08_remote_operator_confirmed"]=False
            with patch.dict(os.environ,{"LLM_API_KEY":"fake-local-offline-token"}):
                plan=make_plan(conf,project_id="no-policy-flag",objective="No network permitted")
                with self.assertRaises(ResearchError):
                    execute_batch(conf,plan)
            m=Memory(conf["database"])
            try:
                self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_queue").fetchone()[0],0)
            finally:m.close()

    def test_genuine_acceptance_without_credentials_is_explicitly_blocked(self):
        from verification.v08_genuine_llm_acceptance import main
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as folder:
            tmp=Path(folder)
            report=tmp/"blocked-report.json"
            db=tmp/"new-v08.sqlite3"
            with patch.dict(os.environ,{"LLM_API_KEY":""}):
                with redirect_stdout(StringIO()):
                    code=main(["--config",str(ROOT/"config/settings.toml"),
                       "--database",str(db),"--report",str(report),
                       "--project-id","genuine-model-required",
                       "--objective","Authorized field relation exploration",
                       "--max-known-usd-per-batch","1.0"])
            self.assertEqual(code,2)
            self.assertEqual(json.loads(report.read_text())["status"],"BLOCKED_OR_FAILED")
            self.assertFalse(db.exists())

    def test_required_tool_call_is_explicit_and_charged(self):
        from alpharesearch.http import Response
        from alpharesearch.llm_provider_policy import PrivateLLMTransport
        with tempfile.TemporaryDirectory() as folder:
            conf=load_settings(ROOT/"config/settings.toml")
            conf["database"]=str(Path(folder)/"provider-tool-choice.sqlite3")
            conf["llm"]=reviewed_llm()
            conf["_v08_remote_operator_confirmed"]=True
            with patch.dict(os.environ,{"LLM_API_KEY":"fake-local-offline-token"}):
                engine=Engine(conf,simulation_limit=0)
                try:
                    agent=authorized_v08_agent(engine)
                    response=Response(200,{},data={"model":"reviewed-model-a",
                        "usage":{"prompt_tokens":15,"completion_tokens":12},"choices":[]})
                    with patch.object(PrivateLLMTransport,"request",return_value=response) as call:
                        agent.complete([{"role":"user","content":"offline protocol"}],
                           [{"type":"function","function":{"name":"test_structured","parameters":{"type":"object","properties":{}}}}],
                           tool_choice="required")
                        self.assertEqual(call.call_args.args[2]["tool_choice"],"required")
                        self.assertEqual(call.call_count,1)
                    charges=engine.memory.db.execute("SELECT input_tokens,output_tokens FROM costs WHERE category='llm'").fetchall()
                    self.assertEqual([(r[0],r[1]) for r in charges],[(15,12)])
                finally:engine.memory.close()

    def test_approved_provider_transport_failure_stops_without_candidate_fallback(self):
        from alpharesearch.http import AmbiguousWrite
        from alpharesearch.llm_provider_policy import PrivateLLMTransport
        with tempfile.TemporaryDirectory() as folder:
            conf=load_settings(ROOT/"config/settings.toml")
            conf["database"]=str(Path(folder)/"v08-isolated.sqlite3")
            conf["llm"]=reviewed_llm()
            conf["_v08_remote_operator_confirmed"]=True
            plan=make_plan(conf,project_id="provider-timeout",objective="One attempt only")
            with patch.dict(os.environ,{"LLM_API_KEY":"fake-local-offline-token"}):
                with patch.object(PrivateLLMTransport,"request",side_effect=AmbiguousWrite("offline expected failure")) as call:
                    with self.assertRaises(AmbiguousWrite):
                        execute_batch(conf,plan)
                    self.assertEqual(call.call_count,1)
            m=Memory(conf["database"])
            try:
                store=Playbook(m)
                self.assertEqual(store.status("provider-timeout")["status"],"paused_fault")
                self.assertEqual(m.db.execute("SELECT COUNT(*) FROM v08_queue").fetchone()[0],0)
                self.assertEqual(m.db.execute("SELECT COUNT(*) FROM trials").fetchone()[0],0)
                events=[json.loads(x[0]) for x in m.db.execute(
                    "SELECT payload FROM events WHERE kind='v08_llm_endpoint_authorized'")]
                self.assertEqual(len(events),1)
                self.assertTrue(events[0]["remote_network_allowed"])
                self.assertNotIn("fake-local-offline-token",json.dumps(events))
            finally:m.close()


if __name__ == "__main__":
    unittest.main()
