"""Campaign v2 integration checks. Run against the REAL v0.6.1 package:
python -m unittest tests/test_campaign_v2.py -v
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alpharesearch.common import ResearchError, load_settings, digest
from alpharesearch.memory import Memory
from alpharesearch.validator import Validator
from alpharesearch.campaign_core import (create_campaign_plan, validate_campaign_plan,
                                         validate_limits, CampaignStore)
from alpharesearch.campaign_runner import (execute_campaign, control_campaign,
                                           import_legacy_ideas)
from alpharesearch._demo_fixtures import CampaignDemoDiscovery
from alpharesearch.campaign_overfit import _pearson, correlation_precheck

ROOT=Path(__file__).resolve().parents[1]

class TestCampaignV2(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config=load_settings(ROOT/"config/settings.toml")
        self.config["database"]=str(Path(self.temp.name)/"research.sqlite3")
        self.config["mode"]="demo"
        self.config["safety"]["live_research_enabled"]=False
        self.config["safety"]["submission_enabled"]=False
        self.spec={"campaign_id":"offline-v2", "objective":"Verify durable L1-L2-L3 scheduler",
                   "generator":"demo", "domains":["price_volume"],
                   "lifecycle":"continuous", "max_total_simulations":None,
                   "ideas_per_cycle":3,"breadth_before_depth":2,
                   "max_depth_per_batch":1}
        self.plan=create_campaign_plan(self.config,self.spec)

    def open_store(self):
        memory=Memory(self.config["database"])
        return memory,CampaignStore(memory)

    def test_continuous_is_null_not_fake_large_number(self):
        self.assertIsNone(self.plan["max_total_simulations"])
        self.assertEqual(self.plan["lifecycle"],"continuous")
        with self.assertRaises(ResearchError):
            create_campaign_plan(self.config,{**self.spec,"max_total_simulations":10000000})
        with self.assertRaises(ResearchError):
            create_campaign_plan(self.config,{**self.spec,"lifecycle":"finite","max_total_simulations":None})

    def test_frozen_plan_cannot_be_edited(self):
        invalid={**self.plan,"max_depth_per_batch":2}
        with self.assertRaises(ResearchError):
            validate_campaign_plan(self.config,invalid)

    def test_lifetime_spans_more_than_six_demo_simulations_without_dropping_queue(self):
        reports=[]
        with patch("alpharesearch.brain.BrainSimulator.start",side_effect=AssertionError("no live")):
            for i in range(6):
                value=execute_campaign(self.config,self.plan,{"max_new_simulations":2})
                reports.append(value)
        self.assertGreater(reports[-1]["simulations"],6)
        self.assertEqual(reports[-1]["status"],"active")
        self.assertEqual(reports[-1]["brain_submits"],0)
        self.assertEqual(reports[-1]["real_brain_simulations"],0)
        self.assertTrue(any(x["counts"]["queued"] for x in reports))
        memory,store=self.open_store()
        try:
            self.assertGreaterEqual(len(store.rows(self.plan["campaign_id"])),6)
            fingerprints=[r["fingerprint"] for r in store.rows(self.plan["campaign_id"])
                          if r["fingerprint"]]
            self.assertEqual(len(fingerprints),len(set(fingerprints)))
            self.assertTrue(store.audit_queue(self.plan["campaign_id"]))
        finally: memory.close()

    def test_same_db_reuses_completed_trial_without_simulation(self):
        one=execute_campaign(self.config,self.plan,{"max_new_simulations":2})
        memory,store=self.open_store()
        try:
            tid=next(x["trial_id"] for x in store.rows("offline-v2") if x["trial_id"])
            self.assertEqual(memory.trial(tid)["state"],"completed")
            saved=memory.latest(tid)
            self.assertTrue(saved and saved.get("synthetic"))
        finally:memory.close()
        two=execute_campaign(self.config,self.plan,{"max_new_simulations":2})
        self.assertGreaterEqual(two["simulations"],one["simulations"])

    def test_legacy_migration_leaves_plan_hash_and_history_untouched(self):
        memory=Memory(self.config["database"])
        try:
            old=memory.create_run(__import__("alpharesearch.common",fromlist=["snapshot"]).snapshot(self.config))
            old_hash="a"*64
            old_id="old-study"
            generator=CampaignDemoDiscovery(self.config)
            fixture=generator.generate(self.plan,[],1)
            memory.transaction(lambda: (
                memory.db.execute("INSERT INTO autonomous_studies VALUES(?,?,?,?)",
                    (old_id,old,old_hash,"2020-01-01")),
                [memory.event(old,"autonomy_idea",{"idea_id":str(i),"idea":v,"state":"queued"})
                 for i,v in enumerate(fixture)]))
            before=memory.run(old)
            event_count=memory.db.execute("SELECT count(*) FROM events WHERE run_id=?",(old,)).fetchone()[0]
        finally:memory.close()
        report=import_legacy_ideas(self.config,self.plan,"old-study")
        self.assertEqual(report["imported"],3)
        second=import_legacy_ideas(self.config,self.plan,"old-study")
        self.assertTrue(second["already_imported"])
        self.assertEqual(second["imported"],0)
        memory,store=self.open_store()
        try:
            self.assertEqual(memory.run(old),before)
            self.assertEqual(memory.db.execute(
                "SELECT plan_hash FROM autonomous_studies WHERE study_id=?",("old-study",)).fetchone()[0],
                old_hash)
            self.assertEqual(memory.db.execute(
                "SELECT count(*) FROM events WHERE run_id=? AND kind LIKE 'autonomy_%'",
                (old,)).fetchone()[0],3)
            self.assertTrue(all(x["state"]=="queued" for x in store.rows("offline-v2")))
        finally:memory.close()

    def test_manual_pause_resume_and_terminate(self):
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        control_campaign(self.config,self.plan,"pause","operator button")
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        control_campaign(self.config,self.plan,"resume","reviewed")
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        control_campaign(self.config,self.plan,"terminate","operator")
        with self.assertRaises(ResearchError):
            control_campaign(self.config,self.plan,"resume","try resurrection")

    def test_failure_pauses_and_requires_operator_resume(self):
        class TimeoutGenerator:
            def generate(self,plan,feedback,cycle):
                raise TimeoutError("simulated model timeout")
        failing=create_campaign_plan(self.config,{**self.spec,"campaign_id":"timeout-fixture",
                                                    "generator":"llm"})
        with self.assertRaises(TimeoutError):
            execute_campaign(self.config,failing,{"max_new_simulations":1},
                             generator=TimeoutGenerator())
        memory,store=self.open_store()
        try:
            self.assertEqual(store.get("timeout-fixture")["status"],"paused_fault")
            self.assertEqual(store.cumulative("timeout-fixture"),0)
        finally:memory.close()
        control_campaign(self.config,failing,"resume","operator_acknowledged_new_request")
        # A nominal LLM campaign cannot quietly restart with fixed Demo formulas.
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,failing,
                {"max_new_simulations":1},generator=CampaignDemoDiscovery(self.config))
        memory,store=self.open_store()
        try:
            self.assertEqual(store.get("timeout-fixture")["status"],"paused_fault")
            self.assertEqual(store.cumulative("timeout-fixture"),0)
        finally: memory.close()

    def test_no_auto_retry_on_existing_incomplete_trial(self):
        memory,store=self.open_store()
        try:
            store.create(self.plan)
            expr=CampaignDemoDiscovery(self.config).generate(self.plan,[],1)[0]
            from alpharesearch.engine import Engine
            engine=Engine(self.config,simulation_limit=0)
            try:
                value=Validator(self.config["catalog_data"],self.config["scope"],
                                self.config["rules"]).validate(expr["expression"])
                tid,_=engine.memory.add_trial(engine.run_id,
                    {"expression":expr["expression"],"hypothesis":"fixture","source":"fixture"},
                    value,engine.context)
            finally:engine.memory.close()
        finally:memory.close()
        outcome=execute_campaign(self.config,self.plan,{"max_new_simulations":2})
        memory,store=self.open_store()
        try:
            trial=memory.trial(tid)
            self.assertEqual(trial["state"],"queued")
            self.assertEqual(memory.run(trial["run_id"])["simulations"],0)
            self.assertTrue(any(x["state"]=="deferred" and "unfinished" in x["reason"]
                                for x in store.rows("offline-v2")))
        finally:memory.close()

    def test_budget_limits_bounded_by_original_program(self):
        rules=self.config["rules"]
        with self.assertRaises(ResearchError):
            validate_limits(self.config,{"max_new_simulations":rules["max_simulations_per_run"]+1})
        self.assertEqual(validate_limits(self.config,{"max_new_simulations":1})["max_new_simulations"],1)

    def test_family_and_sensitivity_do_not_claim_every_candidate(self):
        value=execute_campaign(self.config,self.plan,{"max_new_simulations":3})
        memory,store=self.open_store()
        try:
            self.assertLessEqual(
                memory.db.execute("SELECT count(*) FROM trials WHERE kind='sensitivity'").fetchone()[0],1)
            self.assertGreaterEqual(
                memory.db.execute("SELECT count(*) FROM trials WHERE kind='candidate'").fetchone()[0],2)
        finally:memory.close()

    def test_audit_replay_detects_illicit_mutation(self):
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        memory,store=self.open_store()
        try:
            row=store.rows("offline-v2")[0]
            memory.db.execute("UPDATE campaign_queue SET state='rejected' WHERE id=?",(row["id"],))
            with self.assertRaises(ResearchError):
                store.audit_queue("offline-v2")
        finally:memory.close()

    def test_unknown_llm_token_and_cost_are_not_zero(self):
        memory,store=self.open_store()
        try:
            run_id=memory.create_run(__import__("alpharesearch.common",fromlist=["snapshot"]).snapshot(self.config))
            memory.cost(run_id,"llm",1.3,input_tokens=None,output_tokens=None,
                        usd=None,detail={"outcome":"transport_error","unknown_token_usage":True})
            usage=store.observed_llm_cost(run_id)
            self.assertTrue(usage["unknown_tokens"])
            self.assertTrue(usage["unknown_usd"])
            self.assertEqual(usage["calls"],1)
        finally:memory.close()

    def test_cross_library_readonly_dedup_without_result_import(self):
        other=Path(self.temp.name)/"other.sqlite3"
        cfg={**self.config,"database":str(other)}
        source=Memory(str(other))
        try:
            from alpharesearch.common import research_context
            valid=Validator(cfg["catalog_data"],cfg["scope"],cfg["rules"]).validate(
                    "rank(ts_mean(returns,5))")
            rid=source.create_run(__import__("alpharesearch.common",fromlist=["snapshot"]).snapshot(cfg))
            tid,_=source.add_trial(rid,{"expression":"rank(ts_mean(returns,5))",
                "hypothesis":"test","source":"test"},valid,research_context(cfg))
        finally:source.close()
        memory,store=self.open_store()
        try:
            record=store.external_duplicate([str(other)],research_context(self.config),valid.fingerprint)
            self.assertEqual(record["trial_id"],tid)
            self.assertFalse(record["reusable"])
            readback=Memory(str(other))
            try:
                self.assertEqual(readback.trial(tid)["state"],"queued")
            finally:
                readback.close()
        finally:memory.close()


    def test_campaign_lifetime_simulation_limit_no_overspend(self):
        finite=create_campaign_plan(self.config,{**self.spec,
            "campaign_id":"finite3","lifecycle":"finite","max_total_simulations":3})
        first=execute_campaign(self.config,finite,{"max_new_simulations":2})
        second=execute_campaign(self.config,finite,{"max_new_simulations":2})
        self.assertEqual(second["simulations"],3)
        self.assertEqual(second["status"],"completed")
        self.assertEqual(first["status"],"active")
        self.assertEqual(second["batch_stop_reason"],"finite_lifecycle_cap")

    def test_quality_goal_requires_verified_independent_attestation(self):
        goal=create_campaign_plan(self.config,{**self.spec,
            "campaign_id":"quality-required", "quality_goal_count":2})
        execute_campaign(self.config,goal,{"max_new_simulations":1})
        memory,store=self.open_store()
        try:
            self.assertEqual(store.get("quality-required")["status"],"active")
            with self.assertRaises(ResearchError):
                store.control("quality-required","complete","quality_goal_attested")
            self.assertTrue(store.audit_queue("quality-required"))
        finally:memory.close()

    def test_project_resource_ceiling_pauses_across_runs(self):
        planned=create_campaign_plan(self.config,{**self.spec,
            "campaign_id":"resource-bound","max_total_llm_calls":1,
            "max_total_tokens":10000})
        memory,store=self.open_store()
        try:
            store.create(planned)
            rid=memory.create_run(__import__(
                "alpharesearch.common",fromlist=["snapshot"]).snapshot(self.config))
            store.begin_run("resource-bound",rid,{"max_new_simulations":1})
            memory.consume(rid,"llm_calls",self.config["rules"]["max_llm_calls_per_run"])
            memory.cost(rid,"llm",0.5,input_tokens=20,output_tokens=10,
                        usd=None,detail={"model":"mock"})
        finally:memory.close()
        report=execute_campaign(self.config,planned,{"max_new_simulations":1})
        self.assertEqual(report["status"],"paused_resource")
        self.assertEqual(report["batch_stop_reason"],"paused_resource_max_total_llm_calls")

    def test_audit_detects_unaudited_status_update(self):
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        memory,store=self.open_store()
        try:
            memory.db.execute("UPDATE campaign_projects SET status='completed' "
                              "WHERE campaign_id=?",("offline-v2",))
            with self.assertRaises(ResearchError):
                store.audit_queue("offline-v2")
        finally:memory.close()

    def test_overfit_guards_dont_fake_holdout_proof(self):
        result=correlation_precheck(None, [])
        self.assertEqual(result["status"],"unavailable")
        self.assertEqual(_pearson([1,2,3],[3,2,1]),-1.0)

if __name__=="__main__":
    unittest.main()
