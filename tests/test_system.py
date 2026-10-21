from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date,timedelta
from functools import lru_cache

from alpharesearch.agent import ResearchAgent, TOOLS
from alpharesearch.brain import (BrainSimulator, DemoSimulator, correlation,
    normalize_checks, pnl_changes, records, yearly_stats)
from alpharesearch.common import ResearchError, load_settings
from alpharesearch.engine import Engine
from alpharesearch.evaluator import aligned_correlation, evaluate, shortlist
from alpharesearch.http import AmbiguousWrite, ApiClient, ApiError, PollTimeout, Response, retry_seconds
from alpharesearch.memory import Memory
from alpharesearch.probe import (
    _settings_match,
    inspect_access,
    one_simulation_probe,
    resume_simulation_probe,
)
from alpharesearch.batch import read_candidates
from alpharesearch.validator import InvalidExpression, Validator

ROOT = Path(__file__).resolve().parents[1]


def configure_pnl_contract(config,root,sessions=None):
    if sessions is None:sessions=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range((date(2023,9,30)-date(2020,1,1)).days+1)]
    path=Path(root)/'TEST-calendar.csv'
    with path.open('w',newline='') as out:
        writer=csv.writer(out);writer.writerow(['date']);writer.writerows([[d] for d in sessions])
    contract={'reviewed':True,'review_note':'TEST ONLY synthetic cumulative values, no resets; fixed capital',
        'pnl_unit':'USD','capital_unit':'USD',
        'value_semantics':'cumulative_cash_pnl_constant_capital_session_close','capital_path':['is','bookSize'],
        'calendar_reviewed':True,'calendar_id':'TEST-sessions','calendar_source':'TEST fixture, not exchange data',
        'calendar_review_note':'TEST every supplied date is a session','calendar_region':config['scope']['region'],
        'calendar_start':sessions[0],'calendar_end':sessions[-1],'calendar_file':str(path),
        'calendar_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    config['brain']['pnl_evidence']=contract
    return contract


@lru_cache(maxsize=1)
def fixture_daily():
    from alpharesearch.pnl_evidence import build_daily_evidence
    sessions=[(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range((date(2022,12,31)-date(2020,1,1)).days+1)]
    cumulative=0.;rows=[{'date':sessions[0],'pnl':0.}]
    for i,d in enumerate(sessions[1:]):
        cumulative+=.2+math.sin(i*.71)
        rows.append({'date':d,'pnl':cumulative})
    context={'protocol':'brain-session-pnl-v1','value_semantics':'cumulative_cash_pnl_constant_capital_session_close',
        'pnl_unit':'USD','capital_unit':'USD',
        'contract_sha256':'b'*64,'calendar_id':'TEST-calendar','calendar_sha256':'c'*64,'calendar_region':'USA',
        'calendar_source':'TEST ONLY','calendar_start':sessions[0],'calendar_end':sessions[-1],
        'start_date':sessions[0],'end_date':sessions[-1],'session_dates':sessions,'constant_capital':100.,'capital_path':['is','bookSize']}
    return build_daily_evidence({'records':rows},context)


@lru_cache(maxsize=1)
def fixture_pnl_payload():
    evidence=fixture_daily();days=evidence['daily_evidence']['session_dates']
    total=0.;rows=[{'date':days[0],'pnl':0.}]
    for left,right in zip(days,days[1:]):
        total+=evidence['pnl_changes'][left+'/'+right];rows.append({'date':right,'pnl':total})
    return {'records':rows}


def attach_fixture_return_contract(value,payload=None):
    """Explicit audited metadata for successful TEST ONLY fixtures; no HTTP."""
    from alpharesearch.common import digest
    from alpharesearch.return_contract import brain_return_document,verify_return_document
    payload=fixture_pnl_payload() if payload is None else payload
    value['evidence_source']={'transport':'account_http','alpha_id':value['alpha_id'],
        'account_ref':digest('TEST_ONLY'),'api_base_url':'https://api.worldquantbrain.com',
        'responses':{'pnl':{'method':'GET','payload_sha256':digest(payload)},
                     'alpha':{'method':'GET','payload_sha256':digest({'TEST_ONLY':value['alpha_id'],
                         'expression':value['expression'],'settings':value['settings']})}}}
    config={'brain':{'pnl_evidence':{'reviewed':True,'review_note':'TEST ONLY synthetic cumulative cash',
        'calendar_review_note':'TEST ONLY synthetic every-calendar-day sessions; not market data',
        'costs_reviewed':True,'net_of_costs':False,'cost_model_id':'TEST_ONLY_GROSS',
        'cost_review_note':'TEST ONLY explicit synthetic gross series'}}}
    doc=brain_return_document(config,value,payload)
    value['verified_return_document']=doc
    value['return_verification']=verify_return_document(doc).summary()
    return value


def fixture_result(expression="-rank(ts_delta(close, 5))", scope=None, alpha_id='alpha123'):
    value={"mode": "live", "alpha_id": alpha_id, "expression": expression,
        "settings": scope if scope is not None else {'region':'USA','universe':'TOP3000','delay':1},
        "metrics": {"sharpe": 1.8, "fitness": 1.4, "turnover": 0.2},
        "checks": [{"name": k, "result": "PASS"} for k in
                   ("LOW_SHARPE", "LOW_FITNESS", "CONCENTRATED_WEIGHT",'SELF_CORRELATION')],
        "years": [{"year": str(y), "sharpe": s} for y, s in [(2020, 1.4), (2021, 0.5), (2022, 1.1)]],
        "self_correlation": 0.25, "production_correlation": None,
        **copy.deepcopy(fixture_daily()),
        'pnl_isolation':{'status':'isolated','protocol':'server-is-date-bounds-v1','metadata_source':'alpha_detail.is',
            'start_date':'2020-01-01','end_date':'2022-12-31','contract_sha256':'a'*64},
        "evidence_errors": {}, "collected_at": "2026-10-20T00:00:00+00:00"}
    return attach_fixture_return_contract(value)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, payload=None, headers=None):
        self.calls.append((method, url, payload))
        if not self.responses:
            raise AssertionError("unexpected request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Clock:
    def __init__(self):
        self.value, self.sleeps = 0, []

    def clock(self):
        return self.value

    def sleep(self, value):
        self.sleeps.append(value)
        self.value += value


class FixtureSimulator:
    """TEST ONLY: no HTTP, no performance evidence."""
    mode = "live"

    def __init__(self):
        self.starts = []
        self.expressions = {}
        self.submit_calls = 0

    def start(self, expression, scope):
        self.starts.append(expression)
        location = "https://api.worldquantbrain.com/simulations/s"+str(len(self.starts))
        self.expressions[location] = (expression, scope)
        return location

    def finish(self, location):
        expression, scope = self.expressions[location]
        return fixture_result(expression, scope,alpha_id="alpha"+location.rsplit("s", 1)[1])

    def collect(self, alpha_id):
        for location in self.expressions:
            result = self.finish(location)
            if result["alpha_id"] == alpha_id:
                return result
        raise AssertionError("unknown alpha")

    def submit_once(self, alpha_id):
        self.submit_calls += 1
        return {"alpha_id": alpha_id, "status": "SUBMITTED", "dateSubmitted": "2026-10-20"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load_settings(ROOT/"config/settings.toml")
        self.config['safety']={'live_research_enabled':True,'submission_enabled':True}
        self.config["database"] = str(Path(self.tmp.name)/"research.db")

    def tearDown(self):
        self.doCleanups()
        self.tmp.cleanup()

    def live(self):
        self.config["mode"] = "live"
        self.config["account_profile_verified"] = True
        self.config["catalog_data"]["source"] = "brain-account-api"
        self.config["catalog_data"]["account_id"] = "TEST_ONLY"
        return self.config


class ValidatorTests(Base):
    def setUp(self):
        super().setUp()
        self.validator = Validator(self.config["catalog_data"], self.config["scope"], self.config["rules"])

    def test_baselines_and_neighbors(self):
        for item in json.loads((ROOT/"examples/baseline.json").read_text(encoding="utf-8")):
            original = self.validator.validate(item["expression"])
            neighbors = self.validator.neighbors(item["expression"])
            self.assertEqual(len(neighbors), 2)
            for expr in neighbors:
                self.assertEqual(original.family, self.validator.validate(expr).family)

    def test_rejects_execution_leakage_and_unknowns(self):
        expressions = ["__import__('os').system('id')", "close.__class__", "close[0]", "[x for x in close]",
            "ts_delay(close,-1)", "ts_delay(close,0)", "ts_mean(close, 1+2)", "ts_mean(close,5.0)",
            "ts_mean(close,505)", "rank(future_return)", "rank(close, volume)", "rank(x=close)",
            "close/0", "rank(True)", "a=close;rank(a)", "close**2", "close if 1 else volume",
            "rank(subindustry)", "group_rank(close, volume)", "signed_power(close,100)", "close+1e309"]
        for expr in expressions:
            with self.subTest(expr=expr), self.assertRaises(InvalidExpression):
                self.validator.validate(expr)

    def test_field_point_in_time_and_delay(self):
        self.config["catalog_data"]["fields"]["close"]["point_in_time"] = False
        with self.assertRaises(InvalidExpression):
            self.validator.validate("rank(close)")
        self.config["catalog_data"]["fields"]["close"]["point_in_time"] = True
        self.config["catalog_data"]["fields"]["close"]["min_delay"] = 2
        with self.assertRaises(InvalidExpression):
            self.validator.validate("rank(close)")

    def test_canonicalization_and_family(self):
        a = self.validator.validate("rank(close + volume * 1.0)")
        b = self.validator.validate("rank(1 * volume + close)")
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(self.validator.validate("ts_mean(close,20)").family,
                         self.validator.validate("ts_mean(close,25)").family)
        self.assertNotEqual(self.validator.validate("ts_mean(close,20)").family,
                            self.validator.validate("ts_mean(volume,20)").family)

    def test_complexity(self):
        with self.assertRaises(InvalidExpression):
            self.validator.validate("rank("*30+"close"+")"*30)


class ApiTests(Base):
    def api(self, responses, **kwargs):
        self.transport = ScriptedTransport(responses)
        self.clock = Clock()
        return ApiClient("https://api.worldquantbrain.com", transport=self.transport,
                         sleep=self.clock.sleep, clock=self.clock.clock, **kwargs)

    def test_retry_float_empty_503_and_terminal(self):
        api = self.api([Response(503, {"Retry-After": "1.5"}), Response(200, {"Retry-After": "0.5"}),
                        Response(200, {}, {"progress": 0.8}), Response(200, {}, {"alpha": "ABC"})])
        result = api.wait("/simulations/1", lambda d: "alpha" in d)
        self.assertEqual(result["alpha"], "ABC")
        self.assertEqual(self.clock.sleeps, [1.5, 0.5, 2])
        self.assertTrue(all(x[0] == "GET" for x in self.transport.calls))

    def test_retry_after_not_shortened(self):
        api = self.api([Response(429, {"Retry-After": "75.5"}), Response(200, {}, {"ok": True})])
        api.wait("/operators")
        self.assertEqual(sum(self.clock.sleeps), 75.5)
        self.assertLessEqual(max(self.clock.sleeps), 30)

    def test_timeout_and_auth_stop(self):
        api = self.api([Response(200, {"Retry-After": "100"})], deadline=3)
        with self.assertRaises(PollTimeout):
            api.wait("/simulations/1")
        api = self.api([Response(401, {})])
        with self.assertRaises(ApiError):
            api.wait("/authentication")
        self.assertEqual(len(self.transport.calls), 1)

    def test_cross_origin_downgrade_and_redirect(self):
        api = self.api([])
        for path in ("https://evil.example/simulations/1", "http://api.worldquantbrain.com/simulations/1", "//evil.example/", "https://a:b@api.worldquantbrain.com/x"):
            with self.subTest(path=path), self.assertRaises(ApiError):
                api.url(path)

    def test_post_not_retried(self):
        api = self.api([AmbiguousWrite("TEST timeout")])
        simulator = BrainSimulator(self.config, api)
        with self.assertRaises(AmbiguousWrite):
            simulator.start("rank(close)", self.config["scope"])
        self.assertEqual(len(self.transport.calls), 1)

    def test_simulation_location_and_poll(self):
        api = self.api([Response(201, {"Location": "/simulations/xyz"})])
        simulator = BrainSimulator(self.config, api)
        self.assertTrue(simulator.start("rank(close)", self.config["scope"]).endswith("/simulations/xyz"))
        self.assertEqual(self.transport.calls[0][2]["settings"]["delay"], 1)

    def test_collect_discards_holdout_and_handles_schemas(self):
        configure_pnl_contract(self.config,self.tmp.name)
        self.config['brain']['discovery_evidence']={'schema_reviewed':True,'review_note':'TEST FIXTURE, not real account evidence',
            'date_semantics':'inclusive_dates','is_start_path':['is','startDate'],'is_end_path':['is','endDate']}
        alpha = {"id": "alpha123", "regular": {"code": "rank(close)"}, "settings": self.config["scope"],
                 "is": {"sharpe": 1.8, "fitness": 1.4, "turnover": 0.2,'bookSize':100,'startDate':'2020-01-01','endDate':'2022-12-31'}, "test": {"secret_holdout": 999}, "os": {"secret_holdout": 888}}
        api = self.api([Response(200, {}, alpha), Response(200, {}, {"is": {"checks": fixture_result()["checks"]}}),
            Response(200, {}, {"schema": {"properties": [{"name": "year"}, {"name": "sharpe"}]}, "records": [[2020,1.1],[2021,0.5],[2022,1.2]]}),
            Response(200, {}, {"records": [{"date": "2020-01-01", "pnl": 10}, {"date": "2020-01-02", "pnl": 12}]}),
            Response(200, {}, {"max": 0.3})])
        result = BrainSimulator(self.config, api).collect("alpha123")
        self.assertNotIn("secret_holdout", json.dumps(result))
        self.assertEqual(result["pnl_changes"]["2020-01-01/2020-01-02"], 2)
        self.assertTrue(self.transport.calls[1][1].endswith("/check"))
        self.assertEqual(result["years"][0], {"year": "2020", "sharpe": 1.1})

    def test_unsupported_evidence_does_not_pass(self):
        api = self.api([Response(200, {}, {"id": "alpha123", "is": {"sharpe": 3, "fitness": 3, "turnover": .2}})] + [Response(404, {})]*4)
        result = BrainSimulator(self.config, api).collect("alpha123")
        self.assertEqual(len(result["evidence_errors"]), 4)
        self.assertFalse(evaluate(result, [], self.config["policy"], True)["ready"])

    def test_empty_correlation_not_zero(self):
        for value in ({}, {"max": None}, {"max": float("nan")}, {"max": 1.1}, []):
            with self.assertRaises(ApiError):
                correlation(value)

    def test_submit_503_acceptance_then_get_only(self):
        api = self.api([Response(503, {"Retry-After": "1"}),
            Response(200, {}, {"is": {"checks": [{"result": "PASS"}]}}),
            Response(200, {}, {"id": "alpha123", "status": "ACTIVE", "dateSubmitted": "2026-10-20"})])
        result = BrainSimulator(self.config, api).submit_once("alpha123")
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual([x[0] for x in self.transport.calls], ["POST", "GET", "GET"])

    def test_catalog_pagination_no_next_and_default_disabled(self):
        api = self.api([Response(200, {}, {"count": 2, "results": [{"id": "close", "type": "MATRIX"}]}),
                       Response(200, {}, {"count": 2, "results": [{"id": "volume", "type": "MATRIX"}]}),
                       Response(200, {}, [{"name": "rank"}, {"name": "unknown"}])])
        catalog = BrainSimulator(self.config, api).sync_catalog()
        self.assertEqual(len(catalog["fields"]), 2)
        self.assertEqual(catalog["operators"], ["rank"])
        self.assertFalse(catalog["fields"]["close"]["point_in_time"])
        self.assertIn("offset=1", self.transport.calls[1][1])


class EvaluationTests(Base):
    def test_all_gates_and_no_demo_submit(self):
        good = fixture_result()
        neighbors = [copy.deepcopy(good), copy.deepcopy(good)]
        self.assertTrue(evaluate(good, neighbors, self.config["policy"], True)["ready"])
        self.assertFalse(evaluate(good, neighbors, self.config["policy"], False)["ready"])
        good["mode"] = "demo"
        self.assertFalse(evaluate(good, neighbors, self.config["policy"], True)["ready"])

    def test_high_sharpe_cannot_override_missing_or_failed_gates(self):
        for key in ("checks", "daily_evidence", "self_correlation", "metrics"):
            good = fixture_result()
            good["metrics"]["sharpe"] = 20
            good[key] = None if key == "self_correlation" else {} if key == "metrics" else []
            self.assertFalse(evaluate(good, [fixture_result()]*2, self.config["policy"], True)["ready"])
        for status in ("FAIL", "WARNING", "PENDING", "UNKNOWN"):
            good = fixture_result()
            good["checks"].append({"name": "OTHER_CHECK", "result": status})
            self.assertFalse(evaluate(good, [fixture_result()]*2, self.config["policy"], True)["ready"])

    def test_negative_correlation_and_parameter_instability(self):
        good = fixture_result()
        good["self_correlation"] = -.9
        result = evaluate(good, [fixture_result()]*2, self.config["policy"], True)
        self.assertIn("high_self_correlation", result["rejections"])
        good["self_correlation"] = .2
        weak = fixture_result()
        weak["metrics"]["sharpe"] = .1
        self.assertIn("parameter_sensitive", evaluate(good, [weak]*2, self.config["policy"], True)["rejections"])

    def test_pending_self_check_needs_presubmit_evidence(self):
        good = fixture_result()
        good["checks"][-1]['result']='PENDING'
        self.assertFalse(evaluate(good, [fixture_result()]*2, self.config["policy"], True)["ready"])
        good["self_correlation"] = None
        self.assertFalse(evaluate(good, [fixture_result()]*2, self.config["policy"], True)["ready"])

    def test_diversity_uses_dates_and_absolute_correlation(self):
        good = fixture_result()
        a = {**good, "trial_id": "A", "family": "famA", "evaluation": evaluate(good, [good]*2, self.config["policy"], True)}
        b = copy.deepcopy(a)
        b["trial_id"], b["family"] = "B", "famB"
        b["pnl_changes"] = {k: -v for k,v in b["pnl_changes"].items()}
        chosen, excluded = shortlist([a,b], self.config["policy"])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(len(excluded), 1)
        self.assertIsNone(aligned_correlation({"x":1}, {"y":1}, 1))


class EngineTests(Base):
    def engine(self, live=False, simulator=None):
        if live:
            self.live()
        engine = Engine(self.config, simulator=simulator)
        self.addCleanup(engine.memory.close)
        return engine

    def test_end_to_end_baseline_15_trials(self):
        engine = self.engine()
        for candidate in json.loads((ROOT/"examples/baseline.json").read_text(encoding="utf-8")):
            engine.propose(**candidate)
        report = engine.report()
        self.assertEqual(report["counts"]["simulations"], 15)
        self.assertEqual(len(report["candidates"]), 5)
        self.assertEqual(report["shortlist"], [])
        self.assertEqual(report["task_states"], {"completed":15})
        self.assertFalse(report["independent_holdout_validated"])

    def test_rejected_duplicate_and_scope_dedup(self):
        engine = self.engine()
        engine.propose("ts_delay(close,-5)", "bad", "test")
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 0)
        first = engine.propose("rank(close+volume)", "hyp", "test")
        second = engine.propose("rank(volume + close)", "hyp", "test")
        self.assertEqual(first["trial_id"], second["trial_id"])
        self.assertEqual(second["state"], "duplicate")
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 1)

    def test_budget_includes_neighbors_and_cannot_overshoot(self):
        self.config["rules"]["max_simulations_per_run"] = 2
        engine = self.engine()
        engine.propose("-rank(ts_delta(close,5))", "hyp", "test")
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 2)
        with self.assertRaises(ResearchError):
            engine.propose("rank(volume)", "new", "test")
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 2)

    def test_ambiguous_post_freezes_not_retried(self):
        class Uncertain(DemoSimulator):
            def __init__(self): self.starts = 0
            def start(self, expression, scope):
                self.starts += 1
                raise AmbiguousWrite("TEST ONLY")
        simulator = Uncertain()
        engine = self.engine(simulator=simulator)
        with self.assertRaises(AmbiguousWrite):
            engine.propose("rank(close)", "hyp", "test")
        with self.assertRaises(ResearchError):
            engine.resume()
        self.assertEqual(simulator.starts, 1)
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 1)

    def test_poll_timeout_resume_uses_saved_location(self):
        class Paused(DemoSimulator):
            def __init__(self): self.starts=0; self.finishes=0
            def start(self, expression, scope):
                self.starts += 1
                return super().start(expression, scope)
            def finish(self, location):
                self.finishes += 1
                if self.finishes == 1: raise PollTimeout("TEST ONLY")
                return super().finish(location)
        simulator = Paused()
        engine = self.engine(simulator=simulator)
        with self.assertRaises(PollTimeout):
            engine.propose("rank(close)", "hyp", "test")
        engine.resume()
        self.assertEqual(simulator.starts, 1)
        self.assertEqual(engine.memory.trials(engine.run_id)[0]["state"], "completed")

    def test_crash_gap_before_neighbors_recovers(self):
        engine = self.engine()
        proposal = {"expression":"ts_mean(close,20)", "hypothesis":"hyp", "source":"test"}
        trial_id,_=engine.memory.add_trial(engine.run_id,proposal,engine.validator.validate(proposal["expression"]),engine.context)
        engine.execute(trial_id)
        engine.resume(continue_research=True)
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 3)
        engine.resume(continue_research=True)
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 3)

    def test_immutable_records_and_chain(self):
        engine = self.engine()
        engine.propose("rank(close)", "hyp", "test")
        for table in ("events", "results", "trials"):
            with self.assertRaises(sqlite3.IntegrityError):
                engine.memory.db.execute(f"DELETE FROM {table}")
        self.assertGreater(engine.memory.verify()["events"], 0)

    def test_snapshot_change_blocks_resume(self):
        engine = self.engine()
        changed = copy.deepcopy(self.config)
        changed["policy"]["min_sharpe"] = 0
        with self.assertRaises(ResearchError):
            Engine(changed, run_id=engine.run_id)

    def test_two_workers_cannot_claim_same_task(self):
        engine = self.engine()
        proposal = {"expression":"rank(close)", "hypothesis":"hyp", "source":"test"}
        trial_id,_ = engine.memory.add_trial(engine.run_id,proposal,engine.validator.validate(proposal["expression"]),engine.context)
        second = Memory(self.config["database"])
        self.addCleanup(second.close)
        self.assertTrue(engine.memory.claim(trial_id, 15))
        self.assertFalse(second.claim(trial_id, 15))

    def test_cross_run_dedup_report_and_failed_memory(self):
        simulator = FixtureSimulator()
        first = self.engine(live=True, simulator=simulator)
        first.propose("ts_mean(close,20)", "hyp", "test")
        second = Engine(self.config, simulator=simulator)
        self.addCleanup(second.memory.close)
        outcome = second.propose("ts_mean(close,20)", "hyp", "test")
        self.assertEqual(outcome["state"], "duplicate")
        self.assertEqual(second.memory.run(second.run_id)["simulations"], 0)
        self.assertEqual(len(second.report()["shortlist"]), 1)

    def test_submit_exact_id_review_hash_and_idempotent(self):
        simulator = FixtureSimulator()
        engine = self.engine(live=True, simulator=simulator)
        result = engine.propose("ts_mean(close,20)", "hyp", "test")
        trial_id = result["trial_id"]
        path = Path(self.tmp.name)/"review.json"
        report = engine.write_report(path)
        alpha = report["shortlist"][0]["alpha_id"]
        with self.assertRaises(ResearchError):
            engine.submit(trial_id, path, "wrongID")
        self.assertEqual(simulator.submit_calls, 0)
        self.assertEqual(engine.submit(trial_id, path, alpha)["status"], "SUBMITTED")
        with self.assertRaises(ResearchError):
            engine.submit(trial_id, path, alpha)
        self.assertEqual(simulator.submit_calls, 1)

    def test_tampered_review_and_fresh_failed_check_block_submission(self):
        simulator = FixtureSimulator()
        engine = self.engine(live=True, simulator=simulator)
        result = engine.propose("ts_mean(close,20)", "hyp", "test")
        path = Path(self.tmp.name)/"review.json"
        report = engine.write_report(path)
        alpha = report["shortlist"][0]["alpha_id"]
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["shortlist"][0]["metrics"]["sharpe"] = 99
        path.write_text(json.dumps(tampered))
        with self.assertRaises(ResearchError):
            engine.submit(result["trial_id"], path, alpha)
        engine.write_report(path)
        old = simulator.collect
        def failed(alpha_id):
            value = old(alpha_id)
            value["checks"][0]["result"] = "FAIL"
            return value
        simulator.collect = failed
        with self.assertRaises(ResearchError):
            engine.submit(result["trial_id"], path, alpha)
        self.assertEqual(simulator.submit_calls, 0)

    def test_remote_expression_mismatch_blocks_result(self):
        simulator = FixtureSimulator()
        old = simulator.finish
        def mismatch(location):
            result = old(location)
            result["expression"] = "rank(volume)"
            return result
        simulator.finish = mismatch
        engine = self.engine(live=True, simulator=simulator)
        with self.assertRaises(ApiError):
            engine.propose("rank(close)", "hyp", "test")
        self.assertEqual(engine.memory.trials(engine.run_id)[0]["state"], "paused")
        self.assertEqual(engine.report()["shortlist"], [])

    def test_model_no_submit_shell_or_holdout_tools(self):
        engine = self.engine()
        with patch.dict(os.environ, {"LLM_API_KEY":"TEST_ONLY"}):
            self.config["llm"]["model"] = "TEST_ONLY"
            agent = ResearchAgent(engine)
        names = {x["function"]["name"] for x in TOOLS}
        self.assertEqual(names, {"get_catalog", "get_memory", "propose_candidate", "abandon_candidate", "finish_research",'search_fields','search_papers','record_note','tag_family','get_research_memory'})
        for name in ("submit", "shell", "get_holdout", "edit_evaluator"):
            with self.assertRaises(ResearchError): agent.dispatch(name,{})

    def test_llm_tool_loop_executes_one_agent(self):
        self.config["llm"]["model"] = "TEST_ONLY"
        engine = self.engine()
        def message(calls):
            return Response(200, {}, {"choices":[{"message":{"role":"assistant","content":None,"tool_calls":calls}}]})
        call = {"id":"one", "type":"function", "function":{"name":"propose_candidate","arguments":json.dumps({"expression":"ts_mean(close,20)","hypothesis":"TEST","source":"TEST"})}}
        stop = {"id":"two", "type":"function", "function":{"name":"finish_research","arguments":"{}"}}
        transport = ScriptedTransport([message([call]), message([stop])])
        api = ApiClient("https://example.test/v1", transport=transport)
        with patch.dict(os.environ, {"LLM_API_KEY":"TEST_ONLY"}):
            result = ResearchAgent(engine, api).run("UNTRUSTED TEST PAPER")
        self.assertEqual(result["stopped"], "model_finished")
        self.assertEqual(engine.memory.run(engine.run_id)["llm_calls"], 2)
        self.assertEqual(engine.memory.run(engine.run_id)["simulations"], 3)
        self.assertNotIn("TEST_ONLY", json.dumps(engine.memory.feedback(engine.context)))


class V011RegressionTests(Base):
    def test_pareto_domination_before_family_cap(self):
        def item(tid, turnover):
            return {"trial_id": tid, "family": "same", "self_correlation": 0.2,
                "metrics": {"fitness": 1.4, "turnover": turnover},
                "evaluation": {"ready": True, "worst_window_sharpe": 1.0},
                "pnl_changes": {str(i): math.sin(i) for i in range(50)}}
        # Lexicographic tie-breaker used to put the dominated high-turnover
        # candidate first, and the family cap excluded the winner.
        high, low = item("aaa", 0.5), item("zzz", 0.1)
        selected, excluded = shortlist([high, low], self.config["policy"], family_cap=1)
        self.assertEqual([i["trial_id"] for i in selected], ["zzz"])
        self.assertEqual(excluded[0]["trial_id"], "aaa")

    def test_promote_sensitivity_to_candidate_without_resimulation(self):
        self.config["rules"]["max_simulations_per_run"] = 8
        sim = FixtureSimulator()
        engine = Engine(self.config, simulator=sim)
        self.addCleanup(engine.memory.close)
        first = engine.propose("ts_mean(close,20)", "original", "test")
        self.assertEqual(len(sim.starts), 3)
        child = engine.propose("ts_mean(close,16)", "independent hypothesis", "paper")
        self.assertEqual(child["state"], "duplicate")
        self.assertEqual(len(sim.starts), 5)  # 2 new sensitivity probes, not a repeated 16-day simulation
        report = engine.report()
        selected = [x for x in report["candidates"] if x["expression"].replace(" ", "") == "ts_mean(close,16)"]
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0]["promoted_from_sensitivity"])
        self.assertEqual(selected[0]["hypothesis"], "independent hypothesis")
        self.assertEqual(len(selected[0]["sensitivity"]), 2)
        self.assertEqual(len([x for x in sim.starts if x.replace(" ", "") == "ts_mean(close,16)"]), 1)
        self.assertGreater(engine.memory.verify()["events"], 0)

    def test_promotion_from_prior_run_is_visible_with_new_provenance(self):
        self.config["rules"]["max_simulations_per_run"] = 8
        sim = FixtureSimulator()
        first = Engine(self.config, simulator=sim)
        self.addCleanup(first.memory.close)
        first.propose("ts_mean(close,20)", "old", "test")
        second = Engine(self.config, simulator=sim)
        self.addCleanup(second.memory.close)
        outcome = second.propose("ts_mean(close,16)", "new", "paper")
        self.assertEqual(outcome["state"], "duplicate")
        report = second.report()
        promos = [x for x in report["candidates"] if x["expression"].replace(" ", "") == "ts_mean(close,16)"]
        self.assertEqual(len(promos), 1)
        self.assertEqual(promos[0]["hypothesis"], "new")

    def test_bulk_file_candidate_schema_and_no_execution(self):
        output = Path(self.tmp.name)/"candidates.json"
        output.write_text(json.dumps([
            {"expression":"rank(close)", "hypothesis":"test price", "source":"paper"},
            {"expression":"rank(volume)", "hypothesis":"test volume", "source":"paper"}]))
        rows=read_candidates(output)
        self.assertEqual(len(rows), 2)
        engine=Engine(self.config)
        self.addCleanup(engine.memory.close)
        for row in rows:
            engine.propose(**row)
        self.assertEqual(len(engine.report()["candidates"]), 2)
        output.write_text(json.dumps([{"expression":"rank(close)","hypothesis":"p","source":"p","evil":"ignored"}]))
        with self.assertRaises(ResearchError):
            read_candidates(output)

    def test_read_only_capability_probe_does_not_create_simulations(self):
        transport = ScriptedTransport([
            Response(200, {}, [{"name":"rank"}]),
            Response(200, {}, {"results": [{"id":"close"}]}),
            Response(204, {}, None)])
        api = ApiClient("https://api.worldquantbrain.com", transport=transport)
        sim = BrainSimulator(self.config, api)
        details = inspect_access(sim)
        self.assertTrue(details["operators"]["available"])
        self.assertEqual(details["single_simulation_write_access"], "unverified")
        self.assertEqual([call[0] for call in transport.calls], ["GET", "GET"])

    def test_opt_in_one_simulation_probe_does_one_post_and_audits(self):
        class Stub:
            def __init__(self, scope):
                self.config={"scope":scope}
                self.starts=[]
            def start(self, expression, scope):
                self.starts.append(expression)
                return "https://api.worldquantbrain.com/simulations/single"
            def finish(self, location):
                return {"mode":"live","alpha_id":"alpha001",
                        "metrics":{"sharpe":1.2},"evidence_errors":{}}
        sim = Stub(self.config["scope"])
        output = Path(self.tmp.name)/"probe.json"
        report=one_simulation_probe(sim, "rank(close)", output)
        self.assertEqual(report["state"], "completed")
        self.assertEqual(len(sim.starts), 1)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["alpha_id"], "alpha001")
        with self.assertRaises(FileExistsError):
            one_simulation_probe(sim, "rank(close)", output)
        self.assertEqual(len(sim.starts), 1)

    def test_probe_accepts_server_owned_extra_settings_but_not_changed_scope(self):
        requested = {"region": "USA", "delay": 1, "decay": 4}
        observed = {
            **requested,
            "startDate": "2013-01-01",
            "endDate": "2026-01-01",
        }
        self.assertTrue(_settings_match(observed, requested))
        self.assertFalse(_settings_match({**observed, "delay": 0}, requested))

    def test_write_probe_rejection_leaves_audit_trail_without_retry(self):
        class Stub:
            config = {"scope": {"region":"USA"}}
            count = 0
            def start(self, expression, scope):
                self.count += 1
                raise ApiError("forbidden", 403)
        sim = Stub()
        output = Path(self.tmp.name)/"rejected.json"
        with self.assertRaises(ApiError):
            one_simulation_probe(sim, "rank(close)", output)
        self.assertEqual(sim.count, 1)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["http_status"], 403)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["state"], "rejected")

    def test_resume_probe_only_gets_saved_location(self):
        class Stub:
            def __init__(self, scope):
                self.config={"scope":scope}
                self.api=ApiClient("https://api.worldquantbrain.com", transport=ScriptedTransport([]))
                self.starts=0
                self.finishes=0
            def start(self, expression, scope):
                self.starts+=1
                return "https://api.worldquantbrain.com/simulations/s1"
            def finish(self, location):
                self.finishes+=1
                if self.finishes==1:
                    raise PollTimeout("TEST_ONLY")
                return {"mode":"live", "alpha_id":"x123", "metrics":{}, "evidence_errors":{}}
        sim=Stub(self.config["scope"])
        output=Path(self.tmp.name)/"resume-probe.json"
        with self.assertRaises(PollTimeout):
            one_simulation_probe(sim, "rank(close)", output)
        self.assertEqual(sim.starts, 1)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["state"], "polling_incomplete")
        done=resume_simulation_probe(sim, output)
        self.assertEqual(done["state"], "completed")
        self.assertEqual(sim.starts, 1)

    def test_single_simulation_nested_alpha_result(self):
        transport = ScriptedTransport([Response(200, {}, {"alpha": {"id": "abc123"}})])
        api = ApiClient("https://api.worldquantbrain.com", transport=transport)
        sim = BrainSimulator(self.config, api)
        with patch.object(sim, "collect", return_value={"alpha_id": "abc123"}) as collect:
            result = sim.finish("/simulations/xyz")
        self.assertEqual(result["alpha_id"], "abc123")
        collect.assert_called_once_with("abc123")


if __name__ == "__main__":
    unittest.main()
