#!/usr/bin/env python3
"""Compatibility-contract harness for the additive Campaign v2 files.

Only this patch is executed. The original v0.6.1 source and historical 329
regressions are NOT available in this runtime. These test doubles implement
the public interfaces observed in the raw v0.6.1 source as of 2026-10-24.

NEVER represents a real BRAIN execution, official upstream regression, or
empirical alpha discovery.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
namespace = types.ModuleType("alpharesearch")
namespace.__path__ = [str(ROOT/"alpharesearch")]
sys.modules["alpharesearch"] = namespace

def module(name, **values):
    mod=types.ModuleType("alpharesearch."+name)
    for k, v in values.items(): setattr(mod,k,v)
    sys.modules[mod.__name__]=mod
    return mod

class ResearchError(Exception): pass
def dumps(obj): return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)
def digest(value): return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()
def now(): return "2026-10-24T12:00:00+00:00"
def research_context(config):
    return digest({"scope":config["scope"],"mode":config["mode"],
                   "fields": sorted(config["catalog_data"]["fields"])})
def snapshot(config):
    return {"mode":config["mode"],"scope":config["scope"],
            "rules":config["rules"],"catalog":config["catalog_data"]}
def runtime_params(config):
    return {"llm":{"timeout_seconds":config["llm"]["timeout_seconds"]}}
module("common",ResearchError=ResearchError,dumps=dumps,digest=digest,
       now=now,research_context=research_context,snapshot=snapshot,
       runtime_params=runtime_params)

OPERATOR_SIGNATURES={
    "rank":(1,None),"ts_mean":(2,1),"ts_delta":(2,1),
    "ts_std_dev":(2,1),"ts_corr":(3,2),"ts_zscore":(2,1),
    "ts_sum":(2,1),"ts_decay_linear":(2,1),"ts_rank":(2,1)}
class Validator:
    def __init__(self,catalog,scope,rules):
        self.catalog,self.scope,self.rules=catalog,scope,rules
    def validate(self,expr):
        if not isinstance(expr,str) or not expr or len(expr)>2000:
            raise ResearchError("invalid AST expression")
        try: tree=ast.parse(expr,mode="eval")
        except (SyntaxError,ValueError) as error: raise ResearchError("invalid AST") from error
        fields=set()
        def walk(node):
            if isinstance(node,ast.Name):
                if node.id not in self.catalog["fields"]: raise ResearchError("unknown field")
                fields.add(node.id);return
            if isinstance(node,ast.Constant):
                if type(node.value) not in (int,float): raise ResearchError("non-numeric literal")
                return
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                walk(node.operand);return
            if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
                walk(node.left);walk(node.right);return
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                signature=OPERATOR_SIGNATURES.get(node.func.id)
                if not signature or len(node.args)!=signature[0]:
                    raise ResearchError("unknown operator/arity")
                if signature[1] is not None:
                    window=node.args[signature[1]]
                    if not isinstance(window,ast.Constant) or type(window.value)is not int:
                        raise ResearchError("invalid window literal")
                    if not 1<=window.value<=self.rules["max_window"]:
                        raise ResearchError("future/invalid window")
                for arg in node.args:walk(arg)
                return
            raise ResearchError("illegal AST node "+type(node).__name__)
        walk(tree.body)
        if not fields: raise ResearchError("no fields")
        canonical=ast.unparse(tree)
        family=ast.dump(tree,include_attributes=False)
        return types.SimpleNamespace(
            expression=expr,canonical=canonical,fingerprint=digest(canonical),
            family=digest(family),fields=tuple(sorted(fields)),nodes=sum(1 for _ in ast.walk(tree)))
    def neighbors(self,expr):
        tree=ast.parse(expr,mode="eval")
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                signature=OPERATOR_SIGNATURES.get(node.func.id)
                if signature and signature[1] is not None:
                    window=node.args[signature[1]]
                    if not isinstance(window,ast.Constant): continue
                    old=window.value;result=[]
                    for factor in (0.8,1.2):
                        val=min(self.rules["max_window"],max(1,round(old*factor)))
                        if val!=old:
                            node.args[signature[1]]=ast.Constant(value=val)
                            result.append(ast.unparse(tree))
                    return list(dict.fromkeys(result))[:self.rules["sensitivity_variants"]]
        return []
module("validator",Validator=Validator)

DOMAINS=("price_volume","fundamental","microstructure","sentiment")
IDEA_KEYS={"title","domain","mechanism","expected_sign","horizon_days","falsification",
           "expression","source","family","parent_idea_id","change"}
def approved_fields(config):
    return {name:spec for name,spec in config["catalog_data"]["fields"].items()
            if spec["point_in_time"] and spec["min_delay"]<=config["scope"]["delay"]}
def coverage(config,domains):
    return {d:{"status":"available" if d=="price_volume" else "needs_authorized_fields"}
            for d in domains}
def validate_idea(engine,idea,domains,parents):
    if not isinstance(idea,dict) or set(idea)!=IDEA_KEYS:raise ResearchError("invalid idea keys")
    if idea["domain"] not in domains or idea["family"] not in (
            "reversion","momentum","value","quality","liquidity","volatility","sentiment","carry","other"):
        raise ResearchError("invalid economic family")
    if idea["parent_idea_id"] is not None and idea["parent_idea_id"] not in parents:
        raise ResearchError("invalid parent hypothesis")
    if idea["expected_sign"] not in ("positive","negative","conditional"):
        raise ResearchError("invalid expected_sign")
    for name in ("title","mechanism","falsification","expression","source","change"):
        if not isinstance(idea[name],str) or not idea[name]:raise ResearchError("invalid text field")
    val=engine.validator.validate(idea["expression"])
    if not set(val.fields).intersection(approved_fields(engine.config)):raise ResearchError("no PIT fields")
    return val
class DiscoveryAgent:
    def __init__(self,engine):self.engine=engine;self.model="stub-fast"
    def generate(self,plan,feedback,cycle):raise RuntimeError("No LLM transport in offline harness")
module("discovery",DOMAINS=DOMAINS,IDEA_KEYS=IDEA_KEYS,approved_fields=approved_fields,
       coverage=coverage,validate_idea=validate_idea,DiscoveryAgent=DiscoveryAgent)

class DemoSimulator:
    def start(self, expression, scope):
        return "demo:"+digest({"expression":expression,"scope":scope})
    def finish(self,location):
        n=int(location[-8:],16)
        sh=round(1.0+(n%120)/100,3)
        return {"mode":"demo","synthetic":True,
                "alpha_id":"DEMO_"+location[-10:],
                "metrics":{"sharpe":sh,"fitness":round(sh*0.8,3),"turnover":0.18}}
class BrainSimulator:
    def __init__(self,*args,**kwargs):raise AssertionError("BRAIN access forbidden in harness")
module("brain",DemoSimulator=DemoSimulator,BrainSimulator=BrainSimulator)

class Memory:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),timeout=30,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,created TEXT,snapshot TEXT,
            snapshot_hash TEXT,simulations INTEGER NOT NULL DEFAULT 0,
            proposals INTEGER NOT NULL DEFAULT 0,llm_calls INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
            expression TEXT,hypothesis TEXT,source TEXT,parent_id TEXT,kind TEXT,
            fingerprint TEXT,family TEXT,context TEXT,created TEXT,UNIQUE(context,fingerprint));
        CREATE TABLE IF NOT EXISTS tasks(trial_id TEXT PRIMARY KEY REFERENCES trials(id),state TEXT,
            location TEXT,error TEXT,updated TEXT,account_ref TEXT,api_base_url TEXT,
            dispatched_at TEXT,alpha_id TEXT,remote_state TEXT);
        CREATE TABLE IF NOT EXISTS results(id INTEGER PRIMARY KEY,trial_id TEXT,created TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,created TEXT,run_id TEXT,kind TEXT,
            payload TEXT,previous TEXT,hash TEXT);
        CREATE TABLE IF NOT EXISTS costs(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
            category TEXT,seconds REAL,input_tokens INTEGER,output_tokens INTEGER,
            usd REAL,estimated INTEGER,detail TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS autonomous_studies(study_id TEXT PRIMARY KEY,
            run_id TEXT,plan_hash TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS research_intents(id TEXT PRIMARY KEY,run_id TEXT,trial_id TEXT,
            hypothesis TEXT,source TEXT,parent_intent_id TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS intent_states(intent_id TEXT PRIMARY KEY,state TEXT,reason TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS sensitivity_refs(intent_id TEXT,trial_id TEXT,design_hash TEXT,created TEXT,
            PRIMARY KEY(intent_id,trial_id,design_hash));""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS no_update_events BEFORE UPDATE ON events
                            BEGIN SELECT RAISE(ABORT,'immutable event'); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS no_delete_events BEFORE DELETE ON events
                            BEGIN SELECT RAISE(ABORT,'immutable event'); END""")
    def transaction(self,action):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            value=action();self.db.execute("COMMIT");return value
        except BaseException:
            self.db.execute("ROLLBACK");raise
    def event(self,run_id,kind,payload):
        prev=self.db.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev=prev[0] if prev else ""
        body=dumps(payload)
        sha=digest([now(),run_id,kind,body,prev])
        self.db.execute("INSERT INTO events(created,run_id,kind,payload,previous,hash) VALUES(?,?,?,?,?,?)",
                        (now(),run_id,kind,body,prev,sha))
    def verify(self):
        prev=""
        for row in self.db.execute("SELECT * FROM events ORDER BY id"):
            if row["previous"]!=prev or row["hash"]!=digest(
                    [row["created"],row["run_id"],row["kind"],row["payload"],prev]):
                raise ResearchError("broken append-only chain")
            prev=row["hash"]
        return {"events":self.db.execute("SELECT count(*) FROM events").fetchone()[0],"head":prev}
    def create_run(self,snap,runtime=None):
        rid=uuid.uuid4().hex[:16]
        def work():
            self.db.execute("INSERT INTO runs(id,created,snapshot,snapshot_hash) VALUES(?,?,?,?)",
                            (rid,now(),dumps(snap),digest(snap)))
            self.event(rid,"run_created",snap)
            if runtime is not None:self.event(rid,"runtime_config",{"runtime":runtime})
        self.transaction(work)
        return rid
    def run(self,rid):
        row=self.db.execute("SELECT * FROM runs WHERE id=?",(rid,)).fetchone()
        if row is None:raise ResearchError("unknown run")
        return dict(row)
    def consume(self,rid,counter,limit):
        if counter not in ("proposals","llm_calls"):raise ResearchError("invalid counter")
        def work():
            update=self.db.execute(
                f"UPDATE runs SET {counter}={counter}+1 WHERE id=? AND {counter}<?",
                (rid,limit))
            if update.rowcount!=1:raise ResearchError("counter limit exceeded")
        self.transaction(work)
    def cost(self,rid,category,seconds=0,input_tokens=None,output_tokens=None,
             usd=None,estimated=False,detail=None):
        self.transaction(lambda:self.db.execute(
            "INSERT INTO costs(run_id,category,seconds,input_tokens,output_tokens,usd,estimated,detail,created)"
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (rid,category,seconds,input_tokens,output_tokens,usd,int(estimated),dumps(detail or {}),now())))
    def add_trial(self,rid,proposal,validated,context,kind="candidate",parent_id=None):
        found=self.existing_trial(context,validated.fingerprint)
        if found:return found,False
        tid=uuid.uuid4().hex[:16]
        def work():
            self.db.execute("INSERT INTO trials VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (tid,rid,proposal["expression"],proposal["hypothesis"],
                             proposal["source"],parent_id,kind,validated.fingerprint,
                             validated.family,context,now()))
            self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (tid,"queued",None,None,now(),None,None,None,None,"not_started"))
            self.event(rid,"trial_created",{"trial_id":tid,"expression":proposal["expression"]})
        self.transaction(work)
        return tid,True
    def trial(self,tid):
        row=self.db.execute("SELECT t.*,q.state,q.remote_state FROM trials t "
            "JOIN tasks q ON q.trial_id=t.id WHERE t.id=?",(tid,)).fetchone()
        if not row:raise ResearchError("unknown trial")
        return dict(row)
    def latest(self,tid):
        row=self.db.execute(
            "SELECT payload FROM results WHERE trial_id=? ORDER BY id DESC LIMIT 1",(tid,)).fetchone()
        return json.loads(row[0]) if row else None
    def existing_trial(self,context,fingerprint):
        row=self.db.execute("SELECT id FROM trials WHERE context=? AND fingerprint=?",
                            (context,fingerprint)).fetchone()
        return row[0] if row else None
    def add_intent(self,rid,tid,hypothesis,source,parent_intent_id=None):
        iid=uuid.uuid4().hex[:16]
        def work():
            self.db.execute("INSERT INTO research_intents VALUES(?,?,?,?,?,?,?)",
                            (iid,rid,tid,hypothesis,source,parent_intent_id,now()))
            self.db.execute("INSERT INTO intent_states VALUES(?,?,?,?)",(iid,"active",None,now()))
            self.event(rid,"intent_created",{"intent_id":iid,"trial_id":tid})
        self.transaction(work)
        return iid
    def intents(self,run_id=None,trial_id=None):
        q="SELECT i.*,s.state,s.reason FROM research_intents i JOIN intent_states s ON i.id=s.intent_id WHERE 1=1"
        args=[]
        if run_id:q+=" AND i.run_id=?";args.append(run_id)
        if trial_id:q+=" AND i.trial_id=?";args.append(trial_id)
        return [dict(r) for r in self.db.execute(q,args)]
    def link_sensitivity(self,iid,tid,design_hash):
        def work():
            updated=self.db.execute("INSERT OR IGNORE INTO sensitivity_refs VALUES(?,?,?,?)",
                (iid,tid,design_hash,now())).rowcount
            if updated:self.event(None,"sensitivity_ref",{"intent_id":iid,"trial_id":tid})
        self.transaction(work)
    def close(self):self.db.close()
module("memory",Memory=Memory,
       remote_result_complete=lambda trial,payload,snapshot:False)

class Engine:
    def __init__(self,config,run_id=None,simulator=None,simulation_limit=None):
        self.config=config;self.memory=Memory(config["database"])
        self.run_id=run_id or self.memory.create_run(snapshot(config),runtime_params(config))
        self.context=research_context(config)
        self.validator=Validator(config["catalog_data"],config["scope"],config["rules"])
        self.simulator=simulator or DemoSimulator()
        self.simulation_limit=(config["rules"]["max_simulations_per_run"]
                               if simulation_limit is None else simulation_limit)
    def execute(self,tid):
        t=self.memory.trial(tid)
        if t["run_id"]!=self.run_id:
            return {"trial_id":tid,"state":t["state"],"reused":True}
        if t["state"]!="queued":return {"trial_id":tid,"state":t["state"]}
        def start():
            if self.memory.db.execute("SELECT state FROM tasks WHERE trial_id=?",(tid,)).fetchone()[0]!="queued":
                return False
            row=self.memory.db.execute(
                "UPDATE runs SET simulations=simulations+1 WHERE id=? AND simulations<?",
                (self.run_id,self.simulation_limit))
            if row.rowcount!=1:raise ResearchError("simulation budget exhausted")
            self.memory.db.execute("UPDATE tasks SET state='dispatching' WHERE trial_id=?",(tid,))
            self.memory.event(self.run_id,"simulation_reserved",{"trial_id":tid})
            return True
        if not self.memory.transaction(start):
            return {"trial_id":tid,"state":"already_claimed"}
        location=self.simulator.start(t["expression"],self.config["scope"])
        result=self.simulator.finish(location)
        def finished():
            self.memory.db.execute("INSERT INTO results(trial_id,created,payload) VALUES(?,?,?)",
                                   (tid,now(),dumps(result)))
            self.memory.db.execute("UPDATE tasks SET state='completed',remote_state='completed' "
                                   "WHERE trial_id=?",(tid,))
            self.memory.event(self.run_id,"result_recorded",{"trial_id":tid,"result_sha256":digest(result)})
        self.memory.transaction(finished)
        return {"trial_id":tid,"state":"completed","metrics":result["metrics"]}
    def evaluate_trial(self,tid):
        metrics=(self.memory.latest(tid) or {}).get("metrics",{})
        return {"rejections": [] if metrics.get("fitness",0)>=self.config["policy"]["min_fitness"]
                else ["low_fitness"],"missing":["demo_not_live_evidence"],"ready":False}
module("engine",Engine=Engine)

# Compatible locking API, no credentials.
from contextlib import contextmanager
from threading import Lock
_LOCK=Lock()
@contextmanager
def worker_lock(database):
    if not _LOCK.acquire(blocking=False):raise ResearchError("worker already running")
    try: yield
    finally:_LOCK.release()
module("cli",worker_lock=worker_lock)

from alpharesearch.campaign_core import CampaignStore,create_campaign_plan
from alpharesearch.campaign_runner import (
    CampaignDemoDiscovery,execute_campaign,control_campaign,import_legacy_ideas)

def config(path):
    fields={name:{"kind":"MATRIX","point_in_time":True,
                  "min_delay":1,"research_domains":["price_volume"]}
            for name in ("close","returns","volume","vwap")}
    return {"mode":"demo","database":str(path),"scope":{"region":"USA","universe":"TOP3000",
            "delay":1,"language":"FASTEXPR"},"catalog_data":{"source":"demo",
            "fields":fields,"operators":list(OPERATOR_SIGNATURES)},
            "rules":{"max_simulations_per_run":15,"max_proposals_per_run":30,
                     "max_llm_calls_per_run":40,"sensitivity_variants":2,"max_window":504},
            "policy":{"min_sharpe":1.25,"min_fitness":1.0,
                      "min_sensitivity_neighbors":2,"min_pnl_overlap":252},
            "llm":{"timeout_seconds":90,"model":"offline-placeholder"},
            "safety":{"live_research_enabled":False,"submission_enabled":False}}

def spec(cid="ci-continuous"):
    return {"campaign_id":cid,"objective":"Verify persistent Demo research",
            "domains":["price_volume"],"generator":"demo","lifecycle":"continuous",
            "max_total_simulations":None,"ideas_per_cycle":3,
            "breadth_before_depth":3,"max_depth_per_batch":1}

class OfflineContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=config(Path(self.tmp.name)/"research.sqlite")
        self.plan=create_campaign_plan(self.config,spec())

    def test_six_batches_persist_over_six_simulations(self):
        total=[]
        for i in range(8):
            outcome=execute_campaign(self.config,self.plan,{"max_new_simulations":2,
                                                          "max_steps":12})
            total.append(outcome["simulations"])
            self.assertEqual(outcome["status"],"active")
            self.assertLessEqual(outcome["counts_this_batch"],2)
            self.assertEqual(outcome["brain_submits"],0)
            self.assertEqual(outcome["real_brain_simulations"],0)
        self.assertGreater(total[-1],6)
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            items=store.rows(self.plan["campaign_id"])
            self.assertGreaterEqual(len(items),9)
            self.assertEqual(len(items),len(set(x["id"] for x in items)))
            self.assertEqual(len([i for i in items if i["fingerprint"]]),
                             len(set(i["fingerprint"] for i in items if i["fingerprint"])))
            self.assertTrue(store.audit_queue(self.plan["campaign_id"]))
            stats = dict(memory.db.execute(
                "SELECT kind,count(*) FROM trials GROUP BY kind").fetchall())
            depth_count = memory.db.execute(
                "SELECT count(*) FROM campaign_depth WHERE campaign_id=? AND status='completed'",
                (self.plan["campaign_id"],)).fetchone()[0]
            self.assertGreaterEqual(stats.get("candidate",0),4)
            self.assertGreater(depth_count,0,
                               "L2 must eventually receive simulation budget")
        finally: memory.close()
        print("8 batches totals:",total,"; L1/L2:",stats,"; linked depth:",depth_count)

    def test_pause_resume_terminate_and_frozen_identity(self):
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        control_campaign(self.config,self.plan,"pause","operator")
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        control_campaign(self.config,self.plan,"resume","reviewed")
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        control_campaign(self.config,self.plan,"terminate","operator")
        with self.assertRaises(ResearchError):
            control_campaign(self.config,self.plan,"resume","unacceptable")
        other=create_campaign_plan(self.config,{**spec(),"objective":"different"})
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,other,{"max_new_simulations":1})

    def test_legacy_import_queued_and_idempotence(self):
        memory=Memory(self.config["database"])
        old=memory.create_run(snapshot(self.config))
        fixture=CampaignDemoDiscovery(self.config).generate(self.plan,{},1)
        def record():
            memory.db.execute("INSERT INTO autonomous_studies VALUES(?,?,?,?)",
                              ("old-v061",old,"f"*64,now()))
            for i,v in enumerate(fixture):
                memory.event(old,"autonomy_idea",{"idea_id":"old-"+str(i),
                             "idea":v,"state":"queued","fingerprint":"legacy"})
        memory.transaction(record)
        before_run=memory.run(old)
        before_events=memory.db.execute(
            "SELECT kind,payload,hash FROM events WHERE run_id=? ORDER BY id",(old,)).fetchall()
        before_events=[tuple(r) for r in before_events]
        memory.close()
        migrated=import_legacy_ideas(self.config,self.plan,"old-v061")
        self.assertEqual(migrated["imported"],3)
        once_again=import_legacy_ideas(self.config,self.plan,"old-v061")
        self.assertTrue(once_again["already_imported"])
        memory=Memory(self.config["database"])
        try:
            self.assertEqual(before_run,memory.run(old))
            self.assertEqual(before_events,[tuple(x) for x in memory.db.execute(
                "SELECT kind,payload,hash FROM events WHERE run_id=? ORDER BY id",(old,))])
            store=CampaignStore(memory)
            self.assertEqual(len(store.rows(self.plan["campaign_id"],state="queued")),3)
            self.assertTrue(store.audit_queue(self.plan["campaign_id"]))
        finally:memory.close()

    def test_model_timeout_requires_human_recovery(self):
        plan=create_campaign_plan(self.config,{**spec("timeout-v2"),"generator":"llm"})
        class TimedOut:
            def generate(self,plan,feedback,cycle):raise TimeoutError("model transport")
        with self.assertRaises(TimeoutError):
            execute_campaign(self.config,plan,{"max_new_simulations":1},generator=TimedOut())
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            self.assertEqual(store.get("timeout-v2")["status"],"paused_fault")
            self.assertEqual(store.cumulative("timeout-v2"),0)
        finally:memory.close()
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,plan,{"max_new_simulations":1})
        control_campaign(self.config,plan,"resume","operator_acknowledged_uncertain_request")
        updated=execute_campaign(self.config,plan,{"max_new_simulations":1},
                                 generator=CampaignDemoDiscovery(self.config))
        self.assertEqual(updated["status"],"active")

    def test_finite_campaign_is_not_overspent(self):
        plan=create_campaign_plan(self.config,{**spec("finite-v2"),
              "lifecycle":"finite","max_total_simulations":3})
        first=execute_campaign(self.config,plan,{"max_new_simulations":2})
        last=execute_campaign(self.config,plan,{"max_new_simulations":2})
        self.assertEqual(first["simulations"],2)
        self.assertEqual(last["simulations"],3)
        self.assertEqual(last["status"],"completed")
        self.assertEqual(last["batch_stop_reason"],"finite_lifecycle_cap")

    def test_duplicate_attempt_persisted_and_audited(self):
        memory=Memory(self.config["database"]);store=CampaignStore(memory)
        try:
            store.create(self.plan)
            idea=CampaignDemoDiscovery(self.config).generate(self.plan,{},1)[0]
            val=Validator(self.config["catalog_data"],self.config["scope"],
                          self.config["rules"]).validate(idea["expression"])
            first,created=store.add(self.plan["campaign_id"],idea,1,
                                    validated=val,original_id="0")
            self.assertTrue(created)
            same,created=store.add(self.plan["campaign_id"],idea,2,
                                    validated=val,original_id="1")
            self.assertFalse(created)
            self.assertEqual(first,same)
            self.assertEqual(store.summary(self.plan["campaign_id"])["duplicate_attempts"],1)
            self.assertEqual(store.audit_queue(self.plan["campaign_id"])["duplicate_attempts"],1)
        finally:memory.close()

    def test_offline_preflight_rejects_live(self):
        live={**self.config,"mode":"live"}
        live_plan=create_campaign_plan(live,{**spec("live-v2"),"generator":"llm"})
        with self.assertRaises(ResearchError):
            execute_campaign(live,live_plan,{"max_new_simulations":1})


    def test_one_sim_per_run_still_schedules_selected_depth(self):
        plan=create_campaign_plan(self.config,{**spec("single-budget"),"breadth_before_depth":3})
        with self.assertRaises(ResearchError):
            create_campaign_plan(self.config,{**spec("bad-large"),"max_total_simulations":1000000})
        for _ in range(12):
            outcome=execute_campaign(self.config,plan,{"max_new_simulations":1})
            self.assertEqual(outcome["status"],"active")
        memory=Memory(self.config["database"])
        try:
            kinds=dict(memory.db.execute("SELECT kind,count(*) FROM trials GROUP BY kind").fetchall())
            self.assertGreater(kinds.get("candidate",0),4)
            self.assertGreater(kinds.get("sensitivity",0),0)
        finally: memory.close()

    def test_unknown_llm_usage_not_misrepresented(self):
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            store.create(self.plan)
            rid=memory.create_run(snapshot(self.config))
            store.begin_run(self.plan["campaign_id"],rid,
                            {"max_new_simulations":1,"max_llm_calls":2})
            memory.consume(rid,"llm_calls",self.config["rules"]["max_llm_calls_per_run"])
            memory.cost(rid,"llm",0.4,input_tokens=None,output_tokens=None,usd=None,
                        detail={"outcome":"timeout"})
            costs=store.cumulative_resources(self.plan["campaign_id"])
            self.assertEqual(costs["unknown_token_calls"],1)
            self.assertEqual(costs["unknown_usd_calls"],1)
            self.assertEqual(costs["llm_calls"],1)
            self.assertTrue(store.audit_queue(self.plan["campaign_id"]))
        finally:memory.close()

    def test_cumulative_llm_calls_trigger_resource_pause(self):
        plan=create_campaign_plan(self.config,{**spec("llm-max-one"),
                                               "max_total_llm_calls":1})
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            store.create(plan)
            rid=memory.create_run(snapshot(self.config))
            store.begin_run("llm-max-one",rid,{"max_new_simulations":1})
            memory.consume(rid,"llm_calls",self.config["rules"]["max_llm_calls_per_run"])
        finally:memory.close()
        result=execute_campaign(self.config,plan,{"max_new_simulations":1})
        self.assertEqual(result["status"],"paused_resource")

    def test_frozen_external_db_snapshot_changed_fails_closed(self):
        other=Path(self.tmp.name)/"external.sqlite"
        source=Memory(other)
        try:
            rid=source.create_run(snapshot(self.config))
            expression="rank(ts_mean(returns,5))"
            value=Validator(self.config["catalog_data"],self.config["scope"],
                            self.config["rules"]).validate(expression)
            source.add_trial(rid,{"expression":expression,"hypothesis":"sample",
                                  "source":"sample"},value,research_context(self.config))
        finally:source.close()
        plan=create_campaign_plan(self.config,{**spec("cross-library"),
                "external_libraries":[str(other)]})
        storemem=Memory(self.config["database"])
        try:
            store=CampaignStore(storemem);store.create(plan)
            duplicate=store.external_duplicate([str(other)],research_context(self.config),
                         value.fingerprint,plan["external_library_sha256"])
            self.assertEqual(duplicate["reusable"],False)
            self.assertFalse(duplicate["state"]=="completed")
        finally:storemem.close()
        with other.open("ab") as output:output.write(b"tamper")
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,plan,{"max_new_simulations":1})

    def test_failed_inflight_without_trial_fails_closed(self):
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory);store.create(self.plan)
            idea=CampaignDemoDiscovery(self.config).generate(self.plan,{},1)[0]
            v=Validator(self.config["catalog_data"],self.config["scope"],
                        self.config["rules"]).validate(idea["expression"])
            iid,_=store.add(self.plan["campaign_id"],idea,1,
                            validated=v,original_id="pre-crash")
            store.move(iid,"queued","l1","ready")
            store.move(iid,"testing","l1","crashed_after_preparation")
        finally: memory.close()
        with self.assertRaises(ResearchError):
            execute_campaign(self.config,self.plan,{"max_new_simulations":2})
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            self.assertEqual(store.get(self.plan["campaign_id"])["status"],"paused_fault")
            # The uncertain state is deliberately paused, never silently
            # regenerated or resubmitted to the provider.
            self.assertEqual(store.cumulative(self.plan["campaign_id"]),0)
        finally:memory.close()

    def test_live_rate_governor_is_serial_and_no_remote_network(self):
        from alpharesearch.campaign_live_policy import OfflineRateGovernor
        memory=Memory(self.config["database"])
        try:
            governor=OfflineRateGovernor(memory)
            account_ref=digest("account-fixture")
            policy_hash=digest("rate-limit-test")
            self.assertEqual(governor.reserve(account_ref,"op-1",policy_hash,2,epoch=100.0)["status"],"reserved")
            with self.assertRaises(ResearchError):
                governor.reserve(account_ref,"op-2",policy_hash,2,epoch=100.1)
            governor.mark_uncertain("op-1")
            with self.assertRaises(ResearchError):
                governor.reconcile("op-1","not-op-1")
            with self.assertRaises(ResearchError):
                governor.reconcile("op-1","op-1")
            states=memory.db.execute("SELECT state FROM campaign_rate_reservations").fetchall()
            self.assertEqual(states[0][0],"uncertain")
        finally:memory.close()

    def test_quality_goal_lacks_verified_final_holdout(self):
        plan=create_campaign_plan(self.config,{**spec("quality-strict"),
                                              "quality_goal_count":1})
        for _ in range(3):
            execute_campaign(self.config,plan,{"max_new_simulations":2})
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            with self.assertRaises(ResearchError):
                store.control(plan["campaign_id"],"complete","quality_goal_attested")
            self.assertEqual(store.get(plan["campaign_id"])["status"],"active")
        finally:memory.close()

    def test_walk_forward_policy_frozen(self):
        from alpharesearch.campaign_core import validate_campaign_plan
        corrupt={**self.plan,"walk_forward_embargo":99}
        with self.assertRaises(ResearchError):
            validate_campaign_plan(self.config,corrupt)
        with self.assertRaises(ResearchError):
            create_campaign_plan(self.config,{**spec("overlap"),
                "walk_forward_step":5,"walk_forward_test":126})


    def test_repeated_invalid_or_duplicate_generation_stops_safely(self):
        class OneFormulaOnly:
            def generate(self,plan,feedback,cycle):
                result=CampaignDemoDiscovery(self.config).generate(plan,feedback,1)[0]
                return [dict(result)]
            def __init__(self, config):
                self.config=config
        # First call registers one valid formula, then a second generate
        # produces the same AST and cannot burn unlimited model fees.
        generator=OneFormulaOnly(self.config)
        result=execute_campaign(self.config,self.plan,{"max_new_simulations":3},
                                generator=generator)
        self.assertEqual(result["status"],"active")
        self.assertEqual(result["batch_stop_reason"],
                         "search_stagnated_no_new_valid_candidate")
        self.assertEqual(result["simulations"],1)
        self.assertGreaterEqual(result["duplicate_attempts"],1)
        memory=Memory(self.config["database"])
        try:
            store=CampaignStore(memory)
            self.assertTrue(store.audit_queue(self.plan["campaign_id"]))
        finally:
            memory.close()

    def test_demo_engineering_completion_distinct_from_market_quality(self):
        conf=dict(self.config)
        conf["policy"]={**conf["policy"],"min_sharpe":0.1,
                        "min_fitness":0.1,"min_sensitivity_neighbors":2}
        plan=create_campaign_plan(conf,{**spec("engineering-only"),
                     "demo_engineering_goal_count":1,
                     "quality_goal_count":1,"breadth_before_depth":2,
                     "max_depth_per_batch":1})
        last=None
        for _ in range(20):
            last=execute_campaign(conf,plan,{"max_new_simulations":2})
            if last["status"]=="completed":
                break
        self.assertIsNotNone(last)
        self.assertEqual(last["status"],"completed")
        self.assertEqual(last["reason"],"demo_engineering_goal")
        self.assertEqual(last["quality_goal_status"],
                         "independent_attestation_unavailable_in_demo")
        self.assertFalse(last["empirical_alpha_discovery_validated"])

    def test_unaudited_state_mutation_is_detected(self):
        execute_campaign(self.config,self.plan,{"max_new_simulations":1})
        memory=Memory(self.config["database"]);store=CampaignStore(memory)
        try:
            item=store.rows(self.plan["campaign_id"])[0]
            memory.db.execute("UPDATE campaign_queue SET state='rejected' WHERE id=?",
                              (item["id"],))
            with self.assertRaises(ResearchError):
                store.audit_queue(self.plan["campaign_id"])
        finally:memory.close()

if __name__=="__main__":
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(OfflineContractTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
