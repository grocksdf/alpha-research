"""Frozen, provider-reviewed v0.8.2 tool-call output capabilities.

Model capabilities are empirical/contract evidence, not Alpha formula seeds.
No provider is implicitly compatible merely because its endpoint speaks HTTPS.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from .common import ResearchError, digest
from .agent import build_extra_params

ROLES = ("explorer","strategist","worker","critic","playbook")
MODEL_RE = re.compile(r"[A-Za-z0-9._:/@+\-]{1,160}\Z")
_ALLOWED_OUTPUT_PARAMS = frozenset({"max_tokens","max_completion_tokens"})
_ALLOWED_REASONING_PARAMS = frozenset({"none","reasoning_effort","thinking"})
_FIELDS = frozenset({"model","provider","reviewed_by","review_ref","reviewed_at",
                     "capability_source","max_context_tokens","max_output_tokens",
                     "output_token_parameter","role_output_tokens",
                     "reasoning_parameter","reasoning_value",
                     "tool_choice_required_supported"})
_MAX_GUARD_TOKENS = 131072  # absolute safety limit, NOT a role default


def model_capabilities(config, model, *, clock=None):
    """Fail closed unless exact model, output param and reasoning mode approved.

    The whole profile and source remain inside frozen [llm] snapshot; changing
    any of them invalidates existing research plans and requires a new plan.
    """
    llm=config.get("llm")
    if not isinstance(llm,dict):
        raise ResearchError("冻结的 LLM 配置不合法")
    profiles=llm.get("v08_capabilities")
    if (not isinstance(profiles,dict) or set(profiles)!={"models"} or
            not isinstance(profiles["models"],dict) or
            not 1<=len(profiles["models"])<=12):
        raise ResearchError("真实 v0.8.2 缺少经过审核的模型能力档案")
    if not isinstance(model,str) or model not in profiles["models"]:
        raise ResearchError("未批准的模型无法获得 v0.8.2 输出预算")
    profile=profiles["models"][model]
    if not isinstance(profile,dict) or set(profile)!=_FIELDS:
        raise ResearchError("模型能力档案字段不完整或包含未批准参数")
    for k in ("model","provider","reviewed_by","review_ref","capability_source"):
        val=profile[k]
        if (not isinstance(val,str) or not val.strip() or len(val)>300 or
                val.upper().startswith(("TODO","PLACEHOLDER","REPLACE"))):
            raise ResearchError("能力档案来源或审计字段无效: "+k)
    if profile["model"]!=model or not MODEL_RE.fullmatch(model):
        raise ResearchError("能力档案与冻结请求模型不一致")
    try:
        stamp=profile["reviewed_at"]
        if not isinstance(stamp,str):
            raise ValueError("timestamp not text")
        when=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if when.tzinfo is None:
            raise ValueError("timezone missing")
        when=when.astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError) as exc:
        raise ResearchError("模型能力档案审核时间必须合法且有时区") from exc
    current=datetime.now(timezone.utc) if clock is None else clock
    if current.tzinfo is None or not when<=current<=when+timedelta(days=90):
        raise ResearchError("模型能力档案的审核已过期或尚未生效")
    context=profile["max_context_tokens"]
    cap=profile["max_output_tokens"]
    if (type(context)is not int or type(cap)is not int or
            not 256<=context<=1048576 or
            not 128<=cap<=_MAX_GUARD_TOKENS or cap>context):
        raise ResearchError("模型上下文/单次输出能力必须经过有限上限审核")
    roles=profile["role_output_tokens"]
    if (not isinstance(roles,dict) or set(roles)!=set(ROLES) or
            any(type(x)is not int or not 128<=x<=cap for x in roles.values())):
        raise ResearchError("Explorer/Strategist/Worker/Critic/Playbook 输出能力未逐角色审核")
    if profile["output_token_parameter"] not in _ALLOWED_OUTPUT_PARAMS:
        raise ResearchError("模型输出限制参数未经服务商验证")
    if profile["reasoning_parameter"] not in _ALLOWED_REASONING_PARAMS:
        raise ResearchError("推理参数未获得服务商兼容性审核")
    reasoning=profile["reasoning_value"]
    param=profile["reasoning_parameter"]
    if param=="none":
        if reasoning is not None:
            raise ResearchError("不支持推理参数的模型不可传递推理设置")
    elif param=="reasoning_effort":
        if reasoning not in ("low","medium","high","max","none","xhigh"):
            raise ResearchError("推理档位必须来自受审能力清单")
    elif param=="thinking":
        if not isinstance(reasoning,(dict,str)) or not reasoning:
            raise ResearchError("thinking 必须使用经审核的服务商参数形状")
    if profile["provider"]=="moonshot_direct_kimi_api" and model=="kimi-k3":
        # Official Moonshot K3 API: always-on reasoning, configured via
        # top-level reasoning_effort low/high/max. K2.x `thinking` flags
        # are not interchangeable with K3's parameter.
        if (param!="reasoning_effort" or
                reasoning not in ("low","high","max")):
            raise ResearchError("Moonshot 直连 Kimi K3 不支持未经证实的 thinking/关闭推理参数")
    if profile["tool_choice_required_supported"] is not True:
        raise ResearchError("真实 v0.8.2 需要服务商支持结构化工具调用")
    extras=build_extra_params(llm)
    if any(k in extras for k in ("max_tokens","max_completion_tokens",
                                 "reasoning_effort","thinking")):
        # Avoid conflict with audited per-role dynamic parameters.
        raise ResearchError("真实 v0.8.2 输出/推理参数只能来自冻结能力档案")
    return {**profile,
            "capability_sha256":digest(profile),
            "reviewed_at":when.isoformat()}


def request_parameters(profile, role, output_limit):
    if role not in ROLES:
        raise ResearchError("角色未授权")
    if type(output_limit)is not int or not 128<=output_limit<=profile["role_output_tokens"][role]:
        raise ResearchError("真实 LLM 的请求输出上限超出已冻结能力")
    result={profile["output_token_parameter"]:output_limit}
    param=profile["reasoning_parameter"]
    if param!="none":
        result[param]=profile["reasoning_value"]
    return result
