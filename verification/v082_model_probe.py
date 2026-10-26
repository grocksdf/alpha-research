#!/usr/bin/env python3
"""Opt-in *genuine* tool compatibility probe, with no Alpha formulas or BRAIN.

Example (only in operator-approved environment, with LLM_API_KEY set):
  python verification/v082_model_probe.py --config config/approved.toml \\
      --model kimi-k3 --limits 4096,8192 --max-known-usd 2 \\
      --allow-remote-llm --execute --report state/actual-provider-probe.json

Without --execute or credential the tool makes ZERO network requests.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import sys

if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from alpharesearch.common import ResearchError,load_settings,digest
from alpharesearch.llm_provider_policy import V08LLMClient,validate_llm_endpoint
from alpharesearch.v082_capabilities import model_capabilities,request_parameters
from alpharesearch.v08_pricing import reviewed_model_price,preflight_llm_budget
from alpharesearch.v082_protocol import parse_tool_response,model_usage,safe_model_diagnostic


def probe(config,model,limits,known_usd,*,operator_confirmed=False,execute=False):
    if config["mode"]!="demo" or config["safety"].get("live_research_enabled") or config["safety"].get("submission_enabled"):
        raise ResearchError("模型探针只能在禁用 BRAIN Live / Submit 的 Demo 配置中运行")
    policy=validate_llm_endpoint(config["llm"],operator_confirmed=operator_confirmed)
    profile=model_capabilities(config,model)
    price=reviewed_model_price(config,model)
    if type(known_usd)not in (int,float) or not math.isfinite(known_usd) or not 0<known_usd<=100:
        raise ResearchError("探针费用须有单独有限保护")
    if (not isinstance(limits,list) or not 1<=len(limits)<=4 or
            len(set(limits))!=len(limits) or
            any(type(x) is not int or not 128<=x<=profile["role_output_tokens"]["critic"]
                for x in limits)):
        raise ResearchError("探针上限超出已审核角色/模型能力或重复")
    receipt={
        "protocol":"v082-genuine-model-output-probe-v1",
        "model":model,"endpoint_policy_sha256":policy["policy_sha256"],
        "capability_sha256":profile["capability_sha256"],
        "price_policy_sha256":price["price_policy_sha256"],
        "requested_limits":limits,
        "real_llm_requests":0,"real_brain_requests":0,"submits":0,
        "success_claim_requires_provider_receipt":True,
        "trials":[],"checked_at":datetime.now(timezone.utc).isoformat()}
    if not execute or not os.getenv("LLM_API_KEY"):
        receipt["status"]="BLOCKED_NO_EXECUTE_FLAG_OR_AUTHORIZED_CREDENTIAL"
        return receipt
    client=V08LLMClient(config["llm"],operator_confirmed=operator_confirmed)
    schema={"type":"function","function":{
        "name":"v08_probe",
        "description":"Return a single short compatibility acknowledgement only",
        "parameters":{"type":"object","properties":{
            "status":{"type":"string"},"detail":{"type":"string"}},
            "required":["status","detail"],"additionalProperties":False}}}
    messages=[{"role":"system","content":
               "This is an explicit tool compatibility test, NOT an investment or Alpha task. "
               "Return exactly one tool call with a short plain acknowledgement."},
              {"role":"user","content":"Check structural tool-call compatibility."}]
    remaining_usd=float(known_usd)
    for limit in limits:
        budget=preflight_llm_budget(messages=messages,tool_schema=[schema],
             price=price,config=config,phase="critic",capability=profile,
             remaining_tokens=min(profile["max_context_tokens"],300000),
             remaining_usd=remaining_usd)
        if budget["max_output_tokens"]<limit:
            receipt["status"]="INSUFFICIENT_REVIEWED_BUDGET_FOR_REQUESTED_LIMIT"
            receipt["trials"].append({"requested_limit":limit,
                                       "affordable_limit":budget["max_output_tokens"],
                                       "real_request_sent":False})
            return receipt
        body={"model":model,"messages":messages,"tools":[schema],
              "tool_choice":"required",
              **request_parameters(profile,"critic",limit)}
        result={"requested_limit":limit,"provider_parameter":profile["output_token_parameter"],
                "real_request_sent":True}
        try:
            receipt["real_llm_requests"]+=1
            response=client.once("POST",client.base.rstrip("/")+"/chat/completions",
                                 body,{"Authorization":"Bearer "+os.environ["LLM_API_KEY"]})
            result["http_status"]=response.status
            data=response.data
            result.update(model_usage(data))
            try:
                payload,metadata=parse_tool_response(data,"probe")
                result["valid_tool_call"]=isinstance(payload.get("status"),str)
                result["response_id"]=metadata["response_id"]
                result["finish_reason"]=metadata["finish_reason"]
            except ResearchError as exc:
                result["valid_tool_call"]=False
                result["diagnostic"]=safe_model_diagnostic(
                    data,request_id="probe-"+str(limit),role="probe",
                    model=model,output_limit=limit,
                    output_parameter=profile["output_token_parameter"],
                    error_type=type(exc).__name__)
            prompt=result["prompt_tokens"]
            completion=result["completion_tokens"]
            if prompt is None or completion is None:
                result["estimated_cost_usd"]=None
                result["vendor_usage_unknown"]=True
                receipt["status"]="INCOMPLETE_VENDOR_USAGE_UNKNOWN"
                receipt["trials"].append(result)
                return receipt
            cost=(prompt*price["input_per_million_usd"]+
                  completion*price["output_per_million_usd"])/1e6
            result["estimated_cost_usd"]=cost
            remaining_usd-=cost
            receipt["trials"].append(result)
            if not result["valid_tool_call"]:
                receipt["status"]="INCOMPLETE_INVALID_PROVIDER_TOOL_RESPONSE"
                return receipt
            if remaining_usd<=0:
                receipt["status"]="INCOMPLETE_COST_LIMIT_REACHED"
                return receipt
        except Exception as exc:
            # The request might have reached the provider even without a
            # response; do NOT retry or claim a zero-cost failure.
            result["outcome"]="UNKNOWN_RESPONSE_OR_NETWORK_FAILURE"
            result["error_type"]=type(exc).__name__
            result["vendor_usage_unknown"]=True
            result["estimated_cost_usd"]=None
            receipt["trials"].append(result)
            receipt["status"]="INCOMPLETE_NETWORK_OR_PROVIDER_FAILURE"
            return receipt
    receipt["status"]="PASS_REAL_LLM_TOOL_COMPATIBILITY"
    return receipt


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",required=True)
    p.add_argument("--model",required=True)
    p.add_argument("--limits",required=True)
    p.add_argument("--max-known-usd",type=float,required=True)
    p.add_argument("--allow-remote-llm",action="store_true")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--report",required=True)
    args=p.parse_args(argv)
    path=Path(args.report)
    try:
        requested=[int(x) for x in args.limits.split(",")]
        result=probe(load_settings(args.config),args.model,requested,
                     args.max_known_usd,operator_confirmed=args.allow_remote_llm,
                     execute=args.execute)
    except (ResearchError,OSError,ValueError,TypeError,KeyError) as exc:
        result={"protocol":"v082-genuine-model-output-probe-v1",
                "status":"BLOCKED_OR_FAILED",
                "error_type":type(exc).__name__,
                "reason":str(exc)[:300],
                "real_llm_requests":0,
                "real_brain_requests":0,"submits":0}
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as f:
        f.write(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":result["status"],"report":str(path),
                      "requests":result.get("real_llm_requests",0)},ensure_ascii=False))
    return 0 if result["status"]=="PASS_REAL_LLM_TOOL_COMPATIBILITY" else 2


if __name__=="__main__":
    raise SystemExit(main())
