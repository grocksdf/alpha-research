from __future__ import annotations

import base64
import os
import random
import time
from typing import Any, Self

import httpx

from .models import SimulationResult


class BrainError(RuntimeError):
    pass


def _retry_after(response: httpx.Response, default: float = 2.0) -> float:
    value = response.headers.get("Retry-After")
    try:
        return max(0.1, float(value)) if value is not None else default
    except ValueError:
        return default


class BrainClient:
    def __init__(
        self,
        base_url: str = "https://api.worldquantbrain.com",
        email: str | None = None,
        password: str | None = None,
        timeout_seconds: float = 45,
        poll_timeout_seconds: float = 900,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email or os.getenv("BRAIN_EMAIL")
        self.password = password or os.getenv("BRAIN_PASSWORD")
        self.poll_timeout_seconds = poll_timeout_seconds
        self.max_retries = max_retries
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
            headers={"Accept": "application/json;version=2.0"},
        )
        self.authenticated = False

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def authenticate(self) -> dict[str, Any]:
        if not self.email or not self.password:
            raise BrainError("BRAIN_EMAIL and BRAIN_PASSWORD are required")
        token = base64.b64encode(f"{self.email}:{self.password}".encode()).decode()
        response = self._request(
            "POST", "/authentication", headers={"Authorization": f"Basic {token}"}
        )
        self.authenticated = True
        return self._json(response)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.request(method, url, **kwargs)
                if response.status_code in {429, 502, 503, 504}:
                    if attempt == self.max_retries:
                        response.raise_for_status()
                    time.sleep(_retry_after(response, 2**attempt) + random.random() * 0.2)
                    continue
                if response.status_code == 401 and self.authenticated:
                    self.authenticated = False
                response.raise_for_status()
                return response
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(min(30, 2**attempt) + random.random() * 0.2)
        raise BrainError(f"{method} {url} failed: {last_error}") from last_error

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"results": payload}

    def ensure_authenticated(self) -> None:
        if not self.authenticated:
            self.authenticate()

    def operators(self) -> dict[str, Any]:
        self.ensure_authenticated()
        return self._json(self._request("GET", "/operators"))

    def data_fields(self, **params: Any) -> list[dict[str, Any]]:
        self.ensure_authenticated()
        query = dict(params)
        query.setdefault("limit", 50)
        query.setdefault("offset", 0)
        collected: list[dict[str, Any]] = []
        while True:
            response = self._json(self._request("GET", "/data-fields", params=query))
            values = response.get("results", response.get("data", []))
            page = values if isinstance(values, list) else []
            collected.extend(item for item in page if isinstance(item, dict))
            total = response.get("count") or response.get("total")
            if not page or len(page) < int(query["limit"]):
                break
            query["offset"] = int(query["offset"]) + len(page)
            if isinstance(total, int) and query["offset"] >= total:
                break
        return collected

    def data_field_summary(self, **params: Any) -> dict[str, Any]:
        self.ensure_authenticated()
        return self._json(self._request("GET", "/data-fields/summary", params=params))

    def simulation_options(self) -> dict[str, Any]:
        self.ensure_authenticated()
        return self._json(
            self._request(
                "OPTIONS",
                "/simulations",
                headers={"Accept": "application/json;version=3.0"},
            )
        )

    def simulate(self, expression: str, settings: dict[str, Any]) -> SimulationResult:
        self.ensure_authenticated()
        response = self._request(
            "POST",
            "/simulations",
            json={"type": "REGULAR", "settings": settings, "regular": expression},
        )
        location = response.headers.get("Location")
        initial = self._json(response)
        if not location:
            location = str(initial.get("location") or initial.get("url") or "")
        if not location:
            raise BrainError("simulation response did not include Location")
        simulation = self._poll(location, initial_response=response)
        alpha_id = self._extract_alpha_id(simulation)
        if not alpha_id:
            raise BrainError(f"completed simulation did not include alpha id: {simulation}")
        alpha = self.alpha(alpha_id)
        checks = self.check_alpha(alpha_id)
        return SimulationResult(location, alpha_id, alpha, checks, simulation)

    def _poll(
        self, location: str, initial_response: httpx.Response | None = None
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout_seconds
        response = initial_response
        while time.monotonic() < deadline:
            if response is None or response.request.method != "GET":
                response = self._request("GET", location)
            payload = self._json(response)
            retry_header = response.headers.get("Retry-After")
            status = str(payload.get("status", payload.get("state", ""))).upper()
            if status in {"ERROR", "FAILED", "CANCELLED"}:
                raise BrainError(f"asynchronous job failed: {payload}")
            if (
                retry_header is None
                and status in {"COMPLETE", "COMPLETED", "DONE", "SUCCESS", ""}
                and (self._extract_alpha_id(payload) or status)
            ):
                return payload
            time.sleep(_retry_after(response))
            response = self._request("GET", location)
        raise BrainError(f"polling timed out after {self.poll_timeout_seconds:g}s: {location}")

    @staticmethod
    def _extract_alpha_id(payload: dict[str, Any]) -> str | None:
        alpha = payload.get("alpha")
        if isinstance(alpha, str):
            return alpha.rsplit("/", 1)[-1]
        if isinstance(alpha, dict):
            value = alpha.get("id") or alpha.get("alpha")
            return str(value) if value else None
        for key in ("alphaId", "alpha_id"):
            if payload.get(key):
                return str(payload[key])
        return None

    def alpha(self, alpha_id: str) -> dict[str, Any]:
        self.ensure_authenticated()
        return self._json(self._request("GET", f"/alphas/{alpha_id}"))

    def check_alpha(self, alpha_id: str) -> list[dict[str, Any]]:
        self.ensure_authenticated()
        url = f"/alphas/{alpha_id}/check"
        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            response = self._request("GET", url)
            payload = self._json(response)
            if response.headers.get("Retry-After") is None:
                break
            if time.monotonic() >= deadline:
                raise BrainError(f"alpha checks timed out: {alpha_id}")
            time.sleep(_retry_after(response))
        values = payload.get("checks", payload.get("results", payload.get("data", [])))
        return values if isinstance(values, list) else [payload]

    def submit_alpha(self, alpha_id: str) -> dict[str, Any]:
        self.ensure_authenticated()
        response = self._request("POST", f"/alphas/{alpha_id}/submit")
        location = response.headers.get("Location")
        payload = self._json(response)
        if location or response.headers.get("Retry-After"):
            return self._poll(location or f"/alphas/{alpha_id}/submit", response)
        return payload
