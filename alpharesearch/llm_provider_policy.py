"""Explicit v0.8 research LLM egress policy (never authorizes BRAIN).

A remote provider needs TWO independent opt-ins:
  * frozen, time-limited host/model/data-export approval in [llm.remote_authorization]
  * --allow-remote-llm (or explicit operator_confirmed=True for programmatic runs).

All actual LLM transports disable environment proxies, cookies and redirects.
Remote connections require ordinary CA-verified TLS and exact same-origin paths.
This module never makes an API request by merely validating a policy.
"""
from __future__ import annotations

import http.cookiejar
import math
import re
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .common import ResearchError, digest
from .http import ApiClient, JsonTransport, NoRedirect

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_DNS_NAME = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{5,127}\Z")
_REMOTE_FIELDS = frozenset({"approved_base_url", "approved_models", "approved_by",
                             "approval_ref", "approved_at", "expires_at",
                             "data_export_approved", "approval_note"})


def _model_set(llm):
    models = {llm.get(key) for key in ("model", "fast_model", "reasoning_model")}
    if not isinstance(llm.get("model"), str) or not llm["model"].strip():
        raise ResearchError("v0.8 缺少真实 LLM 模型 ID")
    if any(x is not None and (not isinstance(x, str) or not x.strip() or len(x) > 160)
           for x in models):
        raise ResearchError("v0.8 LLM 模型 ID 无效")
    return sorted(x for x in models if x is not None)


def _parse_expiry(text):
    if not isinstance(text, str):
        raise ResearchError("远程 LLM 授权日期必须带时区")
    try:
        date = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise ResearchError("远程 LLM 授权日期格式错误") from exc
    if date.tzinfo is None or date.utcoffset() is None:
        raise ResearchError("远程 LLM 授权日期缺少时区")
    return date.astimezone(timezone.utc)


def _url_details(value):
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 300:
        raise ResearchError("LLM base_url 非法")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise ResearchError("LLM base_url 包含非法端口或地址") from exc
    if (not url.hostname or url.username is not None or url.password is not None
            or url.query or url.fragment or any(x in value for x in ("\\", "\x00", "\r", "\n"))
            or not url.path.startswith("/") or "//" in url.path or ".." in url.path
            or "%" in url.path or not re.fullmatch(r"/[A-Za-z0-9_./~-]*", url.path)):
        raise ResearchError("LLM 地址不能包含凭据、查询、重定向路径或非标准路径字符")
    host = url.hostname
    if host not in _LOOPBACK and (host.encode("ascii", "ignore").decode("ascii") != host
                                  or not _DNS_NAME.fullmatch(host)):
        raise ResearchError("远程 LLM 只能使用经过审核的规范 DNS 主机名，拒绝 IP/内网/IDN")
    if url.scheme not in ("http", "https"):
        raise ResearchError("LLM 模型服务仅允许 HTTP/HTTPS")
    if host in _LOOPBACK:
        if port is not None and not 1 <= port <= 65535:
            raise ResearchError("本机 LLM 端口无效")
    elif url.scheme != "https" or port not in (None, 443):
        raise ResearchError("远程 LLM 仅允许标准 443 端口上的 HTTPS")
    # Trailing slash normalization is allowed, but not scheme/host/port/path changes.
    return url, host, value.rstrip("/")


def validate_llm_endpoint(llm, *, operator_confirmed=False, clock=None):
    """Validate config without networking or secrets; return redacted receipt."""
    if not isinstance(llm, dict):
        raise ResearchError("llm 配置必须为对象")
    names = _model_set(llm)
    url, host, address = _url_details(llm.get("base_url"))
    if host in _LOOPBACK:
        return {"kind": "loopback", "endpoint_host": host,
                "endpoint_scheme": url.scheme, "models": names,
                "policy_sha256": digest({"base_url": address, "models": names}),
                "remote_network_allowed": False, "brain_access_allowed": False}
    if not operator_confirmed:
        raise ResearchError("远程模型研究必须显式指定 --allow-remote-llm")
    auth = llm.get("remote_authorization")
    if not isinstance(auth, dict) or set(auth) != _REMOTE_FIELDS:
        raise ResearchError("远程 LLM 缺少完整冻结审批记录，禁止外传研究材料")
    approved_url = auth["approved_base_url"]
    _url_details(approved_url)
    if approved_url.rstrip("/") != address:
        raise ResearchError("远程 LLM 地址必须与批准的 HTTPS 端点精确一致")
    approved_models = auth["approved_models"]
    if (not isinstance(approved_models, list) or len(approved_models) > 12
            or any(not isinstance(x, str) or not x.strip() or len(x) > 160
                   for x in approved_models) or len(set(approved_models)) != len(approved_models)
            or not set(names).issubset(approved_models)):
        raise ResearchError("冻结授权的 LLM 模型集合不包含实际请求模型")
    if auth["data_export_approved"] is not True:
        raise ResearchError("未批准将目录/研究目标/记忆发送到外部模型服务")
    for name in ("approved_by", "approval_note"):
        value = auth[name]
        if (not isinstance(value, str) or not 5 <= len(value.strip()) <= 300
                or value.strip().upper().startswith(("TODO", "REPLACE", "PLACEHOLDER"))):
            raise ResearchError("远程模型缺少具体的人工批准记录：" + name)
    ref = auth["approval_ref"]
    if not isinstance(ref, str) or not _REF.fullmatch(ref) or ref.upper().startswith(("TODO", "REPLACE")):
        raise ResearchError("远程模型审批编号无效")
    approved_at = _parse_expiry(auth["approved_at"])
    expires_at = _parse_expiry(auth["expires_at"])
    now = datetime.now(timezone.utc) if clock is None else clock
    if now.tzinfo is None:
        raise ResearchError("安全审批时钟必须具有时区")
    if not (approved_at <= now < expires_at <= approved_at + timedelta(days=90)):
        raise ResearchError("远程 LLM 批准未生效、已过期或期限过长")
    return {"kind": "remote_approved_https", "endpoint_host": host,
            "endpoint_scheme": "https", "models": names,
            "approval_ref": ref, "expires_at": expires_at.isoformat(),
            "policy_sha256": digest({"base_url": address, "models": names,
                                    "remote_authorization": auth}),
            "remote_network_allowed": True, "brain_access_allowed": False}


class PrivateLLMTransport(JsonTransport):
    """No proxy autodiscovery/cookies/redirect; TLS certificate verified."""
    def __init__(self, timeout=30):
        super().__init__(timeout=timeout)
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))


class V08LLMClient(ApiClient):
    """LLM-only transport; accepts loopback HTTP without weakening BRAIN API."""
    def __init__(self, llm, *, operator_confirmed=False):
        receipt = validate_llm_endpoint(llm, operator_confirmed=operator_confirmed)
        timeout = llm.get("timeout_seconds", 90)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 3600:
            raise ResearchError("LLM 网络超时必须为 1..3600 秒")
        self.base = llm["base_url"].rstrip("/") + "/"
        parsed = urlsplit(self.base)
        self.origin = (parsed.scheme, parsed.hostname, parsed.port)
        self.transport = PrivateLLMTransport(timeout)
        self.deadline = timeout
        self.max_attempts = 1  # NEVER retry an uncertain LLM POST.
        self.sleep = lambda _duration: None
        self.clock = __import__("time").monotonic
        self.observer = None
        self.endpoint_policy_receipt = receipt


def authorized_v08_agent(engine):
    """Use the original tools/cost accounting, but with the reviewed endpoint."""
    from .agent import ResearchAgent
    allowed = bool(engine.config.get("_v08_remote_operator_confirmed") is True)
    client = V08LLMClient(engine.config["llm"], operator_confirmed=allowed)
    agent = ResearchAgent(engine, api=client)
    engine.memory.transaction(lambda: engine.memory.event(engine.run_id, "v08_llm_endpoint_authorized", {
        "endpoint_policy_sha256": client.endpoint_policy_receipt["policy_sha256"],
        "endpoint_kind": client.endpoint_policy_receipt["kind"],
        "endpoint_host": client.endpoint_policy_receipt["endpoint_host"],
        "models": client.endpoint_policy_receipt["models"],
        "remote_network_allowed": client.endpoint_policy_receipt["remote_network_allowed"],
        "brain_access_allowed": False,
    }))
    return agent
