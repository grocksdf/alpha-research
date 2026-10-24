"""Offline campaign entry point: python -m alpharesearch.campaign_cli ...

Uses existing config parser and worker lock. No BRAIN imports or logins.
Pause and terminate commands deliberately do not take the worker lock, so they
can interrupt a long-running 'drive' between bounded operations.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .common import ResearchError, load_settings, dumps
from .memory import Memory
from .cli import worker_lock
from .campaign_core import (CampaignStore, create_campaign_plan, validate_campaign_plan,
                            validate_limits)
from .campaign_runner import execute_campaign, control_campaign, import_legacy_ideas

def output(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))

def save_new(path, value):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",encoding="utf-8") as writer:
        writer.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n")

def plan_file(config, path):
    plan=json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_campaign_plan(config, plan)

def main(argv=None):
    parser=argparse.ArgumentParser(description="AlphaResearch offline persistent campaign v2")
    parser.add_argument("--config",default="config/settings.toml")
    commands=parser.add_subparsers(dest="command",required=True)
    create=commands.add_parser("plan",help="Freeze a campaign plan; no network")
    create.add_argument("--spec",required=True);create.add_argument("--output",required=True)
    for name in ("run","drive","inspect","pause","resume","terminate","import-legacy","review-is"):
        p=commands.add_parser(name)
        p.add_argument("--plan",required=True)
        if name in ("run","drive"):
            p.add_argument("--max-new-simulations",type=int,default=3)
            p.add_argument("--max-llm-calls",type=int,default=2)
            p.add_argument("--max-steps",type=int,default=12)
            p.add_argument("--max-wall-seconds",type=int,default=900)
            p.add_argument("--max-tokens",type=int,default=50000)
            p.add_argument("--max-known-usd",type=float)
            p.add_argument("--report-dir",default="state/campaign-reports")
        if name=="drive":
            mode=p.add_mutually_exclusive_group(required=True)
            mode.add_argument("--batches",type=int,help="Positive number of bounded execution batches")
            mode.add_argument("--forever",action="store_true",help="Runs until operator pause/terminate/fault")
            p.add_argument("--sleep-seconds",type=float,default=2.0)
        if name in ("pause","resume","terminate"):
            p.add_argument("--reason",default="operator")
        if name=="import-legacy":
            p.add_argument("--old-study-id",required=True)
        if name=="review-is":
            p.add_argument("--documents",nargs="+",required=True)
            p.add_argument("--train-size",type=int,default=504)
            p.add_argument("--test-size",type=int,default=126)
            p.add_argument("--min-folds",type=int,default=3)
    args=parser.parse_args(argv)
    try:
        config=load_settings(args.config)
        if args.command=="plan":
            plan=create_campaign_plan(config,json.loads(Path(args.spec).read_text(encoding="utf-8")))
            save_new(args.output,plan)
            output({"plan":args.output,"plan_sha256":plan["plan_sha256"],
                    "lifecycle":plan["lifecycle"],"max_total_simulations":plan["max_total_simulations"],
                    "brain_simulation_posts":0,"submit_posts":0})
            return 0
        plan=plan_file(config,args.plan)
        if args.command in ("pause","resume","terminate"):
            # No worker lock. A separate command can stop an active drive.
            output(control_campaign(config,plan,args.command,args.reason))
            return 0
        if args.command=="inspect":
            memory=Memory(config["database"])
            try:
                store=CampaignStore(memory)
                value=store.summary(plan["campaign_id"])
                value["audit"]=store.audit_queue(plan["campaign_id"])
                output(value)
            finally: memory.close()
            return 0
        if args.command=="import-legacy":
            with worker_lock(config["database"]):
                output(import_legacy_ideas(config,plan,args.old_study_id))
            return 0
        if args.command=="review-is":
            from .campaign_evidence import review_campaign_is
            with worker_lock(config["database"]):
                output(review_campaign_is(config,plan,args.documents,
                    train_size=args.train_size,test_size=args.test_size,
                    min_folds=args.min_folds))
            return 0
        limits=validate_limits(config,{
            "max_new_simulations":args.max_new_simulations,
            "max_llm_calls":args.max_llm_calls,"max_steps":args.max_steps,
            "max_wall_seconds":args.max_wall_seconds,
            "max_input_output_tokens":args.max_tokens,"max_known_usd":args.max_known_usd})
        if args.command=="drive":
            if args.batches is not None and args.batches < 1:
                raise ResearchError("--batches 应为正整数")
            if not 0 <= args.sleep_seconds <= 3600:
                raise ResearchError("--sleep-seconds 范围不正确")
        completed=0
        with worker_lock(config["database"]):
            while True:
                report=execute_campaign(config,plan,limits)
                completed+=1
                run_id=report.get("batch_run_id","lifecycle_complete")
                path=Path(args.report_dir)/("%s-%05d-%s.json" %
                    (plan["campaign_id"],completed,run_id))
                save_new(path,report)
                output({"report":str(path),"batch_run_id":run_id,
                        "campaign_status":report["status"],
                        "campaign_simulations":report["simulations"],
                        "batch_simulations":report.get("counts_this_batch",0),
                        "stop_reason":report["batch_stop_reason"],
                        "counts":report["counts"],"real_brain_simulations":0,"brain_submits":0})
                if args.command=="run":
                    return 0
                if report["status"]!="active" or report["batch_stop_reason"] in (
                        "no_new_work_or_authorized_fields","finite_lifecycle_cap"):
                    return 0
                if not args.forever and completed>=args.batches:
                    return 0
                time.sleep(args.sleep_seconds)
    except (ResearchError,OSError,ValueError) as error:
        print("campaign error:",str(error),file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
