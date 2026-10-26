"""v0.8-specific reviewed LLM pricing and *pre-call* budget decisions.

No network, no provider credentials, no BRAIN calls. Price estimates and
provider max_tokens are SOFT spend protections, not preauthorised hard invoices.
Legacy ResearchAgent pricing/Submit gates retain their original behaviour.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from .common import ResearchError, digest, dumps

PRICE_FIELDS = ("input_per_million_usd", "output_per_million_usd")
REVIEW_KEYS = {"source", "applicable_models", "reviewed_at", "reviewed_by",
               "approval_ref", "zero_price_reviewed"}
_ID = re.compile(r"[A-Za-z0-9._:/@+-]{1,160}\Z")


def reviewed_model_price(config, model, *, clock=None, required=True):
    """Classify unknown, reviewed free/mixed rates and reviewed positive rates.

    A zero fee is NOT a synonym for missing prices. Both cases require a
    separately reviewed, time-scoped model-specific pricing record.
    """
    if not isinstance(model, str) or not _ID.fullmatch(model):
        raise ResearchError("LLM 计费适用模型身份无效")
    cost = config.get("cost", {})
    if not isinstance(cost, dict):
        raise ResearchError("LLM 费用配置必须为对象")
    values = [cost.get(k) for k in PRICE_FIELDS]
    valid_rates = all(type(v) in (int,float) and math.isfinite(v) and v >= 0
                      for v in values)
    if not valid_rates:
        if required:
            raise ResearchError("LLM 单价未知或非法：禁止真实付费请求")
        return {"status": "unknown_price", "model": model,
                "input_per_million_usd": None, "output_per_million_usd": None}
    review = cost.get("price_review")
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise ResearchError("缺少审核的模型费用来源/适用模型/审批时间")
    models = review["applicable_models"]
    if (not isinstance(models,list) or not 1 <= len(models) <= 12 or
            any(not isinstance(m,str) or not _ID.fullmatch(m) for m in models)
            or len(set(models)) != len(models) or model not in models):
        raise ResearchError("价格审批的适用模型与实际请求模型不匹配")
    for key in ("source","reviewed_by","approval_ref"):
        value = review[key]
        if (not isinstance(value,str) or not 6 <= len(value.strip()) <= 300
                or value.strip().upper().startswith(("TODO","REPLACE","PLACEHOLDER"))):
            raise ResearchError("LLM 价格审批信息无效: "+key)
    try:
        timestamp = review["reviewed_at"]
        if not isinstance(timestamp,str):
            raise ValueError("not string")
        reviewed_at = datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError("timezone")
        reviewed_at = reviewed_at.astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError) as error:
        raise ResearchError("LLM 价格审核时间必须是有时区的 ISO8601") from error
    now = datetime.now(timezone.utc) if clock is None else clock
    if now.tzinfo is None or not reviewed_at <= now <= reviewed_at + timedelta(days=90):
        raise ResearchError("LLM 单价审核未生效或已过期")
    if type(review["zero_price_reviewed"]) is not bool:
        raise ResearchError("零单价的审核标志必须明确为布尔值")
    if 0 in values and not review["zero_price_reviewed"]:
        raise ResearchError("零单价未经批准，不能视为免费或已知费用")
    status = ("reviewed_zero" if all(v==0 for v in values) else
              "reviewed_mixed_zero" if 0 in values else "reviewed_nonzero")
    return {"status":status,"model":model,
            "input_per_million_usd":float(values[0]),
            "output_per_million_usd":float(values[1]),
            "source":review["source"],"reviewed_at":reviewed_at.isoformat(),
            "reviewed_by":review["reviewed_by"],
            "approval_ref":review["approval_ref"],
            "applicable_models":list(models),
            "price_policy_sha256":digest({"review":review, "input":values[0],
                                          "output":values[1]})}


def preflight_llm_budget(*, messages, tool_schema, price, config,
                         remaining_tokens, remaining_usd,
                         phase=None, capability=None):
    """Locally reject unbudgetable prompts and bound response max_tokens.

    The token count for unknown provider tokenisation, overhead, hidden
    reasoning, and invoice charges is NOT knowable from local JSON bytes;
    these are conservative best-effort estimates ONLY. USD is a soft limit.
    """
    if type(remaining_tokens) is not int or remaining_tokens <= 0:
        raise ResearchError("单批 token 已用尽")
    if type(remaining_usd) not in (float,int) or not math.isfinite(remaining_usd) or remaining_usd <= 0:
        raise ResearchError("必须有剩余的有限 USD 预算，禁止无界请求")
    if price.get("status") not in ("reviewed_zero", "reviewed_mixed_zero", "reviewed_nonzero"):
        raise ResearchError("未审核单价，不得开始模型请求")
    llm = config["llm"]
    from .agent import build_extra_params
    extras = build_extra_params(llm)
    if capability is not None:
        if phase not in capability["role_output_tokens"]:
            raise ResearchError("角色能力缺少审查")
        configured_max = capability["role_output_tokens"][phase]
        provider_cap = capability["max_output_tokens"]
        context_cap = capability["max_context_tokens"]
        parameter = capability["output_token_parameter"]
    else:
        # Legacy direct preflight tests still need a reviewed finite local cap;
        # real LLMRoles always passes an explicit frozen capability profile.
        configured_max = extras.get("max_tokens")
        if type(configured_max) is not int or not 128<=configured_max<=131072:
            raise ResearchError("真实 LLM 的逐角色输出上限必须来自冻结能力档案")
        provider_cap = configured_max
        context_cap = config.get("llm",{}).get("v08_reviewed_context_limit",configured_max+65536)
        parameter = "max_tokens"
    # Role/tool schema adds prompt tokens, as do provider wrappers and
    # invisible reasoning; byte length is NOT an exact tokenizer measurement.
    payload_bytes = len(dumps({"messages":messages,"tools":tool_schema}).encode("utf-8"))
    estimated_input = payload_bytes + 1024
    if remaining_tokens <= estimated_input + 127:
        raise ResearchError("剩余 token 不足以为请求和结构化回复留出空间")
    if type(context_cap)is not int or context_cap<=estimated_input+127:
        raise ResearchError("输入上下文估算超出经审核的服务商上下文容量")
    output_limit = min(configured_max,provider_cap,
                       context_cap-estimated_input,
                       remaining_tokens-estimated_input)
    in_rate = price["input_per_million_usd"]
    out_rate = price["output_per_million_usd"]
    # The 80% allowance retains a margin, but is NOT a guaranteed invoice cap.
    budget_soft_allowance = remaining_usd * 0.8
    estimated_prompt_cost = estimated_input * in_rate / 1e6
    if estimated_prompt_cost >= budget_soft_allowance and in_rate > 0:
        raise ResearchError("预计输入费用已超过预留软预算，不发起模型请求")
    if out_rate > 0:
        max_by_usd = math.floor(max(0, budget_soft_allowance-estimated_prompt_cost)
                                * 1e6/out_rate)
        output_limit = min(output_limit,max_by_usd)
    if output_limit < 128:
        raise ResearchError("剩余 token / 费用不足以完成有效结构化模型回复")
    return {
        "max_output_tokens":int(output_limit),
        "output_token_parameter":parameter,
        "role":phase,
        "capability_sha256":capability.get("capability_sha256") if capability else None,
        "estimated_prompt_token_upper_proxy":int(estimated_input),
        "input_token_count_type":"estimated_utf8_bytes_proxy_not_provider_tokens",
        "hidden_reasoning_tokens":"unknown_until_provider_usage",
        "provider_output_usage_is_completion_tokens_including_reported_reasoning":True,
        "estimated_prompt_usd":estimated_prompt_cost,
        "soft_usd_allowance":budget_soft_allowance,
        "priced_model":price["model"],
        "price_policy_sha256":price["price_policy_sha256"],
        "local_call_and_token_preflight_hard":True,
        "provider_invoice_hard_capped":False,
        "remaining_tokens_before_request":remaining_tokens,
        "remaining_known_usd_before_request":remaining_usd,
    }
