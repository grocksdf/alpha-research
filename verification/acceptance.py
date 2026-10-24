#!/usr/bin/env python3
"""Full-repository, offline, fail-closed AlphaResearch Campaign v2 QA.

Runs original regression suite AND isolated multi-batch campaign acceptance.
Never loads BRAIN credentials, invokes live endpoints or creates a real POST.
Requires a complete checked-out upstream repository; does not create one.

Example:
  python verification/acceptance.py --repo . --config config/settings.toml
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from unittest import mock

def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def immutable_report(out, data):
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    serialized=json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n"
    with out.open("x",encoding="utf-8") as handle:
        os.chmod(out,0o600)
        handle.write(serialized)
        handle.flush();os.fsync(handle.fileno())

def acceptance(root,config_file):
    from alpharesearch.common import ResearchError, load_settings, snapshot, digest
    from alpharesearch.memory import Memory
    from alpharesearch.campaign_core import CampaignStore,create_campaign_plan
    from alpharesearch.campaign_runner import execute_campaign
    config=load_settings(config_file)
    # Never silently convert a Live configuration into Demo and imply it was
    # tested. A dedicated Demo config is required for this acceptance suite.
    if config["mode"]!="demo":
        raise ResearchError("离线验收只接受 mode='demo' 的专用配置")
    if config.get("safety",{}).get("live_research_enabled") or config.get(
            "safety",{}).get("submission_enabled"):
        raise ResearchError("离线验收拒绝真实研究/提交已启用的配置")
    if config["rules"]["max_simulations_per_run"] < 2:
        raise ResearchError("验收至少需要每批两次独立模拟额度")
    from alpharesearch.discovery import approved_fields
    if not {"returns","close","volume"}<=set(approved_fields(config)):
        raise ResearchError("离线 demo 目录必须包含已授权的 close/returns/volume")

    with tempfile.TemporaryDirectory(prefix="alpha-campaign-v2-acceptance-") as tmp:
        config={**config,"database":str(Path(tmp)/"isolated.sqlite3")}
        spec={"campaign_id":"offline_acceptance_v2",
              "objective":"Offline quality gates: multi-run persistence and stage allocations",
              "domains":["price_volume"],"generator":"demo",
              "lifecycle":"continuous","max_total_simulations":None,
              "ideas_per_cycle":3,"queue_low_watermark":2,
              "breadth_before_depth":3,"max_depth_per_batch":1}
        plan=create_campaign_plan(config,spec)
        results=[]
        start_snapshot=digest(snapshot(config))
        from alpharesearch.brain import BrainSimulator
        from alpharesearch.engine import Engine
        from alpharesearch.http import ApiClient
        def forbidden(*args,**kwargs):
            raise AssertionError("OFFLINE QA attempted a BRAIN API or Submit call")
        # Keep class identity intact for isinstance() inside Engine.execute().
        with mock.patch.object(BrainSimulator,"start",forbidden), \
             mock.patch.object(BrainSimulator,"finish",forbidden), \
             mock.patch.object(Engine,"submit",forbidden), \
             mock.patch.object(ApiClient,"once",forbidden):
            for batch_no in range(8):
                result=execute_campaign(config,plan,
                    {"max_new_simulations":2,"max_llm_calls":2,
                     "max_steps":15,"max_wall_seconds":300})
                assert result["status"]=="active",result
                assert result["counts_this_batch"]<=2,result
                assert result["simulations"]==2*(batch_no+1),result
                assert result["real_brain_simulations"]==0
                assert result["brain_submits"]==0
                results.append({"batch":batch_no+1,
                                "run_id":result["batch_run_id"],
                                "simulations":result["simulations"],
                                "stop_reason":result["batch_stop_reason"],
                                "counts":result["counts"]})
        memory=Memory(config["database"])
        try:
            store=CampaignStore(memory)
            audit=store.audit_queue(plan["campaign_id"])
            ideas=store.rows(plan["campaign_id"])
            trials=[dict(x) for x in memory.db.execute(
                "SELECT kind,context,fingerprint,state FROM trials "
                "JOIN tasks ON tasks.trial_id=trials.id")]
            base=sum(v["kind"]=="candidate" for v in trials)
            depth=sum(v["kind"]=="sensitivity" for v in trials)
            if base<5 or depth<1:
                raise AssertionError("Expected L1 breadth and selective L2 depth")
            fingerprints=[v["fingerprint"] for v in ideas if v["fingerprint"]]
            assert len(fingerprints)==len(set(fingerprints)), "duplicate campaign formula"
            assert store.get(plan["campaign_id"])["status"]=="active"
            assert store.cumulative(plan["campaign_id"])==16
            assert audit["campaign_batches"]==8
            assert digest(snapshot(config))==start_snapshot
            return {
                "status":"PASS",
                "acceptance_mode":"isolated_real_v061_code_demo",
                "source":"real_imports_from_user_checkout_not_test_double",
                "base_trials":base,"sensitivity_trials":depth,
                "total_simulations":16,"batch_results":results,
                "queue_items":len(ideas),"audit":audit,
                "real_brain_simulation_posts":0,"brain_submit_posts":0,
                "snapshot_sha256":start_snapshot,
                "independent_market_alpha_proven":False,
            }
        finally:
            memory.close()

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",default=".")
    parser.add_argument("--config",default="config/settings.toml")
    parser.add_argument("--report")
    parser.add_argument("--timeout-seconds",type=int,default=600)
    args=parser.parse_args(argv)
    root=Path(args.repo).resolve()
    configfile=(root/args.config).resolve()
    relative=configfile.is_relative_to(root)
    if not relative or not configfile.is_file():
        parser.error("Configuration must be inside repo and must exist")
    if not (root/"alpharesearch/campaign_runner.py").is_file():
        parser.error("Campaign v2 files are not installed")
    sys.path.insert(0,str(root))
    try:
        from alpharesearch.common import load_settings
        preflight=load_settings(configfile)
        if (preflight["mode"]!="demo" or
                preflight.get("safety",{}).get("live_research_enabled") or
                preflight.get("safety",{}).get("submission_enabled")):
            parser.error("Offline acceptance requires Demo and disabled Live/Submit safety")
    except (KeyError, OSError, ValueError, ImportError) as error:
        parser.error("Unable to validate isolated Demo configuration: "+type(error).__name__)
    report=Path(args.report).resolve() if args.report else root/"state"/(
        "campaign-acceptance-"+dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".json")
    facts={"protocol":"alpha-campaign-v2-acceptance-v1",
           "checked_at":dt.datetime.now(dt.timezone.utc).isoformat(),
           "repo":str(root),"config_relative_path":str(configfile.relative_to(root)),
           "original_regressions":{"status":"NOT_RUN"},
           "integration_acceptance":{"status":"NOT_RUN"},
           "original_files_modified_by_qa":False,
           "git_commit":None,"report_sha256":None}
    try:
        commit=subprocess.run(["git","rev-parse","HEAD"],cwd=str(root),text=True,
                    capture_output=True,timeout=8,check=False)
        if commit.returncode==0:
            facts["git_commit"]=commit.stdout.strip()
    except (OSError,subprocess.TimeoutExpired):
        pass
    raw_checks = [
        ("alpharesearch/autonomy.py","alpharesearch/campaign_core.py"),
        ("alpharesearch/engine.py","alpharesearch/campaign_runner.py"),
        ("alpharesearch/memory.py","alpharesearch/campaign_live_policy.py"),
    ]
    facts["source_sha256"]={
        item:digest_bytes((root/item).read_bytes())
        for group in raw_checks for item in group
        if (root/item).is_file()}
    # Original tests run in a separate process. Live endpoints are NOT
    # activated by this script; user should run on a dedicated Demo checkout.
    if args.timeout_seconds < 10 or args.timeout_seconds > 3600:
        parser.error("--timeout-seconds must be 10..3600")
    test_command=[sys.executable,"-m","unittest","discover",
                  "-s","tests","-p","test_*.py","-v"]
    started=time.monotonic()
    try:
        safe_env={k:v for k,v in os.environ.items()
                  if not (k.startswith("BRAIN_") or
                          k in ("LLM_API_KEY","OPENAI_API_KEY"))}
        safe_env.update(ALPHARESEARCH_OFFLINE_ACCEPTANCE="1",
                        PYTHONDONTWRITEBYTECODE="1")
        subprocess_result=subprocess.run(test_command,cwd=str(root),
            capture_output=True,text=True,timeout=args.timeout_seconds,
            check=False,env=safe_env)
        log=(subprocess_result.stdout+"\n"+subprocess_result.stderr)[-200000:]
        logfile=report.with_suffix(".regression.log")
        # A separate, never-overwritten log accompanies the audit JSON.
        with logfile.open("x",encoding="utf-8") as handler:
            os.chmod(logfile,0o600);handler.write(log);handler.flush();os.fsync(handler.fileno())
        match=re.search(r"Ran (\d+) tests? in",log)
        facts["original_regressions"]={
            "status":"PASS" if subprocess_result.returncode==0 and
                         "OK" in log and match and
                         int(match.group(1))>=329 else "FAIL",
            "exit_code":subprocess_result.returncode,
            "test_count":int(match.group(1)) if match else None,
            "duration_seconds":round(time.monotonic()-started,3),
            "log_sha256":digest_bytes(log.encode("utf-8")),
            "log_path":str(logfile)}
    except subprocess.TimeoutExpired:
        facts["original_regressions"]={
            "status":"TIMEOUT","timeout_seconds":args.timeout_seconds}
    except OSError as error:
        facts["original_regressions"]={"status":"ERROR","type":type(error).__name__}
    if facts["original_regressions"]["status"]=="PASS":
        sys.path.insert(0,str(root))
        try:
            facts["integration_acceptance"]=acceptance(root,configfile)
        except BaseException as error:
            facts["integration_acceptance"]={"status":"FAIL",
                       "error_type":type(error).__name__,
                       "description":str(error)[:1000]}
    else:
        facts["integration_acceptance"]={
            "status":"BLOCKED_BY_ORIGINAL_REGRESSIONS",
            "reason":"No integration claims after failing original tests"}
    facts["overall_status"]="PASS" if (
        facts["original_regressions"]["status"]=="PASS" and
        facts["integration_acceptance"]["status"]=="PASS") else "FAIL_OR_INCOMPLETE"
    facts["real_brain_access_performed_by_acceptance"]=False
    facts["real_alpha_performance_established"]=False
    facts["report_sha256"]=digest_bytes(json.dumps({
        k:v for k,v in facts.items() if k!="report_sha256"},
        ensure_ascii=False,sort_keys=True,default=str).encode("utf-8"))
    immutable_report(report,facts)
    print(json.dumps({"report":str(report),"overall_status":facts["overall_status"],
              "old_regressions":facts["original_regressions"]["status"],
              "isolated_campaign":facts["integration_acceptance"]["status"],
              "real_brain_posts":0,"submit_posts":0},ensure_ascii=False,indent=2))
    return 0 if facts["overall_status"]=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
