"""Strict OpenAI-compatible, provider-independent v0.8.2 tool reply checks.

Never reinterpret text-only, truncated or partially formed JSON as a model
tool call. Diagnostics store only hashes and small safe metadata.
"""
from __future__ import annotations

import hashlib
import json
import re

from .common import ResearchError, dumps


class ModelOutputTruncated(ResearchError):
    """Provider terminated the response because output quota was reached."""


class ModelToolCallMissing(ResearchError):
    """No authenticated structured function call was present."""


class ModelToolCallInvalid(ResearchError):
    """Malformed/unsanctioned function call or arguments."""


def model_usage(data):
    if not isinstance(data,dict):
        return {"prompt_tokens":None,"completion_tokens":None,
                "reasoning_tokens":None,"reasoning_usage_source":"unknown"}
    usage=data.get("usage")
    if not isinstance(usage,dict):
        usage={}
    details=usage.get("completion_tokens_details")
    details=details if isinstance(details,dict) else {}
    def nonnegative(v):
        return v if type(v) is int and v>=0 else None
    return {
        "prompt_tokens":nonnegative(usage.get("prompt_tokens")),
        "completion_tokens":nonnegative(usage.get("completion_tokens")),
        "reasoning_tokens":nonnegative(details.get("reasoning_tokens")),
        "reasoning_usage_source":("provider_reported" if
           nonnegative(details.get("reasoning_tokens")) is not None else "unknown")}


def safe_model_diagnostic(data,*,request_id,role,model,output_limit,
                          output_parameter,error_type):
    """No content, headers, tool arguments, chain of thought or credentials."""
    choice=None
    if isinstance(data,dict):
        choices=data.get("choices")
        if isinstance(choices,list) and choices and isinstance(choices[0],dict):
            choice=choices[0]
    reason=choice.get("finish_reason") if isinstance(choice,dict) else None
    msg=choice.get("message") if isinstance(choice,dict) else None
    calls=msg.get("tool_calls") if isinstance(msg,dict) else None
    ident=data.get("id") if isinstance(data,dict) else None
    if not isinstance(ident,str) or not re.fullmatch(r"[A-Za-z0-9._:/@+\\-]{1,160}",ident):
        ident=None
    safe_reason=reason if reason in ("length","stop","tool_calls","content_filter") else None
    try:
        response_sha=hashlib.sha256(dumps(data).encode("utf-8")).hexdigest()
    except (TypeError,ValueError):
        response_sha=None
    return {
        "request_id":request_id,"role":role,"model":model,
        "response_id":ident,"finish_reason":safe_reason,
        "output_token_limit":output_limit,
        "output_token_parameter":output_parameter,
        "number_of_tool_calls":len(calls) if isinstance(calls,list) else None,
        "response_sha256":response_sha,
        "response_body_persisted":False,
        "reasoning_content_persisted":False,
        "error_type":error_type,
        **model_usage(data)}


def parse_tool_response(data,role,*,max_argument_bytes=100000):
    """Return (parsed JSON object, call metadata); never access raw keys blindly."""
    if not isinstance(data,dict):
        raise ModelToolCallMissing("LLM 响应非 JSON 对象，缺少工具调用")
    choices=data.get("choices")
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):
        raise ModelToolCallMissing("LLM choices 缺失、类型错误或非唯一")
    choice=choices[0]
    reason=choice.get("finish_reason")
    if reason=="length":
        raise ModelOutputTruncated("供应商 finish_reason=length；输出已截断，不能接受提案")
    if reason not in ("stop","tool_calls",None):
        raise ModelToolCallMissing("供应商未完成合法工具调用")
    if reason is None:
        raise ModelToolCallMissing("供应商 finish_reason 缺失：无法确定工具响应已完成")
    message=choice.get("message")
    if not isinstance(message,dict):
        raise ModelToolCallMissing("LLM message 缺失或不是对象")
    calls=message.get("tool_calls")
    if not isinstance(calls,list) or not calls:
        raise ModelToolCallMissing("LLM 未返回工具调用；不接受普通文本")
    if len(calls)!=1 or not isinstance(calls[0],dict):
        raise ModelToolCallInvalid("LLM 必须恰好返回一个结构化工具调用")
    call=calls[0]
    func=call.get("function")
    if not isinstance(func,dict) or func.get("name")!="v08_"+role:
        raise ModelToolCallInvalid("LLM 返回未授权工具或工具名称不匹配")
    call_id=call.get("id")
    if not isinstance(call_id,str) or not call_id.strip() or len(call_id)>160:
        raise ModelToolCallInvalid("LLM 工具调用缺少有效来源 ID")
    raw=func.get("arguments")
    if not isinstance(raw,str) or not 1<=len(raw.encode("utf-8"))<=max_argument_bytes:
        raise ModelToolCallInvalid("LLM 工具参数不是合法有界字符串")
    try:
        obj=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("non-finite JSON constant")))
    except (ValueError,TypeError,RecursionError) as exc:
        raise ModelToolCallInvalid("LLM 工具 JSON 无法完整解析") from exc
    if not isinstance(obj,dict):
        raise ModelToolCallInvalid("LLM 工具参数必须为 JSON 对象")
    return obj, {
        "response_id":data.get("id"),
        "tool_call_id":call_id,
        "original_raw_sha256":hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "finish_reason":reason}
