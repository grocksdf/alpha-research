from __future__ import annotations

import http.cookiejar
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from .common import ResearchError, dumps


class ApiError(ResearchError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class AmbiguousWrite(ApiError):
    """A POST may have reached BRAIN. Never automatically resend it."""


class PollTimeout(ApiError):
    pass


@dataclass
class Response:
    status: int
    headers: dict
    data: object = None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonTransport:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method, url, payload=None, headers=None):
        body = None if payload is None else dumps(payload).encode()
        request = urllib.request.Request(url, body, method=method, headers={
            "Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
        try:
            result = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            result = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            error = AmbiguousWrite if method == "POST" else ApiError
            raise error("网络响应未收到；写操作禁止自动重发" if method == "POST" else "网络连接失败") from e
        with result:
            try:
                raw = result.read(8_000_001)
            except (TimeoutError, OSError) as e:
                error = AmbiguousWrite if method == "POST" else ApiError
                raise error("读取响应失败；写操作禁止自动重发" if method == "POST" else "读取 API 响应失败") from e
            hdr = {k.lower(): v for k, v in result.headers.items()}
            if len(raw) > 8_000_000:
                raise ApiError("API 响应超过大小限制")
            try:
                data = json.loads(raw) if raw.strip() else None
            except (ValueError, UnicodeError):
                data = {"_non_json": True}
            return Response(result.status, hdr, data)


def retry_seconds(value, default=2.0):
    if value is None:
        return default
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return default
    return max(0.05, seconds) if math.isfinite(seconds) else default


class ApiClient:
    def __init__(self, base_url, timeout=30, deadline=900, max_attempts=100,
                 transport=None, sleep=time.sleep, clock=time.monotonic, observer=None):
        self.base = base_url.rstrip("/") + "/"
        parsed = urlsplit(self.base)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname or parsed.query or parsed.fragment:
            raise ApiError("API base_url 必须是无凭据的 HTTPS 地址")
        self.origin = (parsed.scheme, parsed.hostname, parsed.port)
        self.transport = transport or JsonTransport(timeout)
        self.deadline, self.max_attempts, self.sleep, self.clock = deadline, max_attempts, sleep, clock
        self.observer=observer

    def url(self, path):
        url = urljoin(self.base, path)
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.hostname, parsed.port) != self.origin or parsed.username or parsed.password or parsed.fragment:
            raise ApiError("拒绝跨域/降级 Location，避免泄露会话")
        return url

    def once(self, method, path, payload=None, headers=None):
        response = self.transport.request(method, self.url(path), payload, headers)
        response.headers = {k.lower(): v for k, v in response.headers.items()}
        if self.observer: self.observer(method,self.url(path),response)
        if response.status in (401, 403):
            raise ApiError("需要人工登录、验证或账户权限确认；不绕过验证码", response.status)
        return response

    def wait(self, path, terminal=None):
        end = self.clock() + self.deadline
        for _ in range(self.max_attempts):
            response = self.once("GET", path)
            if response.status in (404, 405):
                raise ApiError("账户接口不可用，需核实 API 配置", response.status)
            transient = response.status in (429, 503, 502, 504)
            if not transient and not 200 <= response.status < 300:
                raise ApiError(f"GET API HTTP {response.status}", response.status)
            if not transient and response.data is not None and "retry-after" not in response.headers:
                if terminal is None or terminal(response.data):
                    return response.data
            delay = retry_seconds(response.headers.get("retry-after"))
            if self.clock() + delay >= end:
                break
            # Full server wait is respected; split to keep interruptibility.
            while delay > 0:
                piece = min(30.0, delay)
                self.sleep(piece)
                delay -= piece
        raise PollTimeout("异步查询超时；保留任务，可恢复轮询")
