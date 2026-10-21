"""P0 parameter-surface regressions: [llm.extra_params] passthrough and the
runtime/research snapshot split. No live credentials or HTTP; scripted transport only."""
import json
import os
from unittest.mock import patch

from test_system import Base, ScriptedTransport
from alpharesearch.agent import ResearchAgent, build_extra_params
from alpharesearch.autonomy import create_plan, validate_plan
from alpharesearch.common import ResearchError, digest, runtime_params, snapshot
from alpharesearch.engine import Engine
from alpharesearch.http import ApiClient, Response
from alpharesearch.memory import Memory


def finishing_transport():
    return ScriptedTransport([Response(200, {}, {"choices": [{"message": {"role": "assistant", "content": None,
        "tool_calls": [{"id": "one", "type": "function", "function": {"name": "finish_research", "arguments": "{}"}}]}}]})])


class ExtraParamTests(Base):
    """P0-a: provider request parameters pass through a strict whitelist."""

    def agent(self):
        engine = Engine(self.config)
        self.addCleanup(engine.memory.close)
        self.config["llm"]["model"] = "TEST_ONLY"
        transport = finishing_transport()
        api = ApiClient("https://example.test/v1", transport=transport)
        with patch.dict(os.environ, {"LLM_API_KEY": "TEST_ONLY"}):
            return ResearchAgent(engine, api), transport

    def test_no_extra_params_keeps_base_payload(self):
        agent, transport = self.agent()
        agent.run("")
        self.assertEqual(set(transport.calls[0][2]), {"model", "messages", "tools", "tool_choice"})

    def test_thinking_passthrough_reaches_provider(self):
        self.config["llm"]["extra_params"] = {"thinking": {"type": "disabled"}}
        agent, transport = self.agent()
        agent.run("")
        payload = transport.calls[0][2]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(set(payload), {"model", "messages", "tools", "tool_choice", "thinking"})

    def test_config_cannot_override_tool_surface_or_messages(self):
        for bad in ({"tools": []}, {"messages": []}, {"tool_choice": "none"}, {"model": "SWAPPED"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ResearchError):
                    build_extra_params({"extra_params": bad})

    def test_rejected_unknown_and_mistyped_params(self):
        for bad in ({"thinking": 5}, {"temperature": "high"}, {"temperature": True},
                    {"top_p": None}, {"max_tokens": 0}, {"max_tokens": True}, {"max_tokens": 1.5},
                    {"reasoning_effort": []}, "not-a-table"):
            with self.subTest(bad=bad):
                with self.assertRaises(ResearchError):
                    build_extra_params({"extra_params": bad})

    def test_accepted_param_shapes(self):
        self.assertEqual(build_extra_params({}), {})
        self.assertEqual(build_extra_params({"extra_params": None}), {})
        self.assertEqual(build_extra_params({"extra_params": {"reasoning_effort": "high"}}), {"reasoning_effort": "high"})
        self.assertEqual(build_extra_params({"extra_params": {"reasoning_effort": 0.5}}), {"reasoning_effort": 0.5})
        self.assertEqual(build_extra_params({"extra_params": {"max_tokens": 4096, "temperature": 0.2, "top_p": 0.9, "thinking": "disabled"}}),
                         {"max_tokens": 4096, "temperature": 0.2, "top_p": 0.9, "thinking": "disabled"})

    def test_extra_params_are_frozen_research_identity(self):
        before = digest(snapshot(self.config))
        self.config["llm"]["extra_params"] = {"thinking": {"type": "disabled"}}
        self.assertNotEqual(digest(snapshot(self.config)), before)


class RuntimeParamSplitTests(Base):
    """P0-b: operational parameters adjust with audit; model identity and rules stay frozen."""

    def test_timeout_change_does_not_change_frozen_snapshot(self):
        before = digest(snapshot(self.config))
        self.config["llm"]["timeout_seconds"] = 999
        self.assertEqual(digest(snapshot(self.config)), before)
        self.assertEqual(runtime_params(self.config), {"llm": {"timeout_seconds": 999}})

    def test_identity_change_still_changes_frozen_snapshot(self):
        before = digest(snapshot(self.config))
        self.config["llm"]["model"] = "SWAPPED_MODEL"
        self.assertNotEqual(digest(snapshot(self.config)), before)

    def test_frozen_plan_survives_timeout_change_but_not_model_change(self):
        spec = {"study_id": "p0-split", "objective": "TEST ONLY", "domains": ["price_volume"],
                "max_simulations": 1, "max_cycles": 1, "max_ideas": 1, "ideas_per_cycle": 1, "generator": "llm"}
        plan = create_plan(self.config, spec)
        self.config["llm"]["timeout_seconds"] = 300
        self.assertEqual(validate_plan(self.config, plan), plan)
        self.config["llm"]["model"] = "SWAPPED_MODEL"
        with self.assertRaises(ResearchError):
            validate_plan(self.config, plan)

    def test_runtime_params_recorded_and_adjustment_audited(self):
        memory = Memory(self.config["database"])
        self.addCleanup(memory.close)
        original = self.config["llm"]["timeout_seconds"]
        snap = snapshot(self.config)
        memory.create_run(snap, runtime_params(self.config))
        self.config["llm"]["timeout_seconds"] = original + 150
        memory.create_run(snap, runtime_params(self.config))
        rows = [json.loads(r[0]) for r in memory.db.execute(
            "SELECT payload FROM events WHERE kind='runtime_config' ORDER BY id")]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["runtime"], {"llm": {"timeout_seconds": original}})
        self.assertFalse(rows[0]["adjusted"])
        self.assertIsNone(rows[0]["previous_runtime"])
        self.assertEqual(rows[1]["runtime"], {"llm": {"timeout_seconds": original + 150}})
        self.assertTrue(rows[1]["adjusted"])
        self.assertEqual(rows[1]["previous_runtime"], {"llm": {"timeout_seconds": original}})
        memory.verify()

    def test_run_creation_without_runtime_still_works(self):
        memory = Memory(self.config["database"])
        self.addCleanup(memory.close)
        run_id = memory.create_run(snapshot(self.config))
        self.assertIsNone(memory.db.execute(
            "SELECT 1 FROM events WHERE run_id=? AND kind='runtime_config'", (run_id,)).fetchone())
        memory.verify()
