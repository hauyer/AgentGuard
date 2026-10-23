"""AgentGuard's integration boundary to the authoritative WARDEN audit kernel.

AgentGuard owns policy, approval, experience retrieval and user-facing orchestration.
WARDEN owns probe/SAE resources, audit-plan compilation, causal-audit execution and
low-level evidence. This module intentionally contains no model hooks or causal
inference implementation.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


class WardenKernelError(RuntimeError):
    """Sanitized failure raised at the AgentGuard/WARDEN boundary."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str = "WARDEN_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except URLError as exc:
        reason = type(exc.reason).__name__
        raise WardenKernelError(
            f"WARDEN connection failed ({reason})", code="WARDEN_UNREACHABLE"
        ) from None
    except TimeoutError:
        raise WardenKernelError("WARDEN request timed out", code="WARDEN_TIMEOUT") from None
    except OSError as exc:
        raise WardenKernelError(
            f"WARDEN transport failed ({type(exc).__name__})", code="WARDEN_TRANSPORT_ERROR"
        ) from None


class WardenKernelClient:
    """Small, typed-by-contract HTTP client for WARDEN's versioned `/v1` API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        local_http = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local_http):
            raise ValueError("WARDEN base_url must use HTTPS; plain HTTP is allowed only for loopback")
        if not parsed.netloc:
            raise ValueError("WARDEN base_url must include a host")
        if not 1 <= float(timeout) <= 120:
            raise ValueError("WARDEN timeout must be between 1 and 120 seconds")
        self.base_url = normalized
        self.timeout = float(timeout)
        self._transport = transport or _urllib_transport

    @classmethod
    def from_env(cls, *, transport: Transport | None = None) -> "WardenKernelClient | None":
        """Return None when WARDEN is not configured; never silently start a local kernel."""
        base_url = os.environ.get("AGENTGUARD_WARDEN_URL", "").strip()
        if not base_url:
            return None
        try:
            timeout = float(os.environ.get("AGENTGUARD_WARDEN_TIMEOUT", "15"))
            return cls(base_url, timeout=timeout, transport=transport)
        except ValueError as exc:
            raise WardenKernelError(
                f"Invalid WARDEN configuration: {str(exc)[:120]}",
                code="WARDEN_CONFIGURATION_ERROR",
            ) from None

    @staticmethod
    def _expect_object(value: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise WardenKernelError(
                f"WARDEN {endpoint} response is not an object", code="WARDEN_BAD_RESPONSE"
            )
        return value

    def health(self) -> dict[str, Any]:
        return self._expect_object(self._request("GET", "/v1/health"), "/v1/health")

    @staticmethod
    def _expect_object_list(value: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise WardenKernelError(
                f"WARDEN {endpoint} response is not a list of objects", code="WARDEN_BAD_RESPONSE"
            )
        return value

    def capabilities(self) -> list[dict[str, Any]]:
        value = self._request("GET", "/v1/capabilities")
        return self._expect_object_list(value, "/v1/capabilities")

    def resources(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind not in {None, "probe", "sae"}:
            raise ValueError("resource kind must be 'probe', 'sae', or None")
        suffix = "" if kind is None else "?" + urlencode({"kind": kind})
        value = self._request("GET", "/v1/resources" + suffix)
        return self._expect_object_list(value, "/v1/resources")

    def contracts(self) -> list[str]:
        value = self._request("GET", "/v1/contracts")
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise WardenKernelError(
                "WARDEN contracts response is not a list of strings", code="WARDEN_BAD_RESPONSE"
            )
        return value

    def create_audit_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._expect_object(
            self._request("POST", "/v1/audit-plans", dict(payload)), "/v1/audit-plans"
        )

    def get_audit_plan(self, plan_id: str) -> dict[str, Any]:
        path = f"/v1/audit-plans/{quote(plan_id, safe='')}"
        return self._expect_object(self._request("GET", path), path)

    def start_run(self, plan_id: str) -> dict[str, Any]:
        return self._expect_object(self._request("POST", "/v1/runs", {"plan_id": plan_id}), "/v1/runs")

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = f"/v1/runs/{quote(run_id, safe='')}"
        return self._expect_object(self._request("GET", path), path)

    def submit_audit(
        self, payload: Mapping[str, Any], *, start_run: bool = True
    ) -> dict[str, Any]:
        """Create a plan and optionally submit it only when WARDEN says it is runnable.

        A blocked plan is returned as blocked; it is never translated into a clean result.
        """
        plan = self.create_audit_plan(payload)
        status = str(plan.get("status", "unknown")).lower()
        result: dict[str, Any] = {"plan": plan, "run": None}
        if not start_run or status != "planned":
            result["submission_status"] = "blocked" if status == "blocked" else status
            return result
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            raise WardenKernelError(
                "WARDEN plan response is missing plan_id", code="WARDEN_BAD_RESPONSE"
            )
        run = self.start_run(plan_id)
        result["run"] = run
        result["submission_status"] = str(run.get("status", "unknown")).lower()
        return result

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        status, raw = self._transport(
            method,
            self.base_url + path,
            {"Accept": "application/json", "Content-Type": "application/json"},
            body,
            self.timeout,
        )
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WardenKernelError(
                "WARDEN returned a non-JSON response", status_code=status, code="WARDEN_BAD_RESPONSE"
            ) from None
        if status < 200 or status >= 300:
            detail = value.get("detail") if isinstance(value, dict) else None
            if isinstance(detail, dict):
                detail = detail.get("error") or detail.get("code")
            safe_detail = str(detail)[:180] if detail else "request rejected"
            raise WardenKernelError(
                f"WARDEN API HTTP {status}: {safe_detail}",
                status_code=status,
                code="WARDEN_HTTP_ERROR",
            )
        return value


def warden_status(client: WardenKernelClient | None = None) -> dict[str, Any]:
    """Report integration state without conflating missing infrastructure with low risk."""
    if client is None:
        try:
            client = WardenKernelClient.from_env()
        except WardenKernelError as exc:
            return {
                "status": "misconfigured",
                "kernel": "WARDEN",
                "base_url": os.environ.get("AGENTGUARD_WARDEN_URL") or None,
                "error_code": exc.code,
                "reason": str(exc),
                "authoritative_for": ["probe", "sae", "causal_audit", "low_level_evidence"],
            }
    if client is None:
        return {
            "status": "not_configured",
            "kernel": "WARDEN",
            "authoritative_for": ["probe", "sae", "causal_audit", "low_level_evidence"],
            "base_url": None,
            "reason": "Set AGENTGUARD_WARDEN_URL to enable the WARDEN kernel integration.",
        }
    try:
        health = client.health()
        return {
            "status": "available" if health.get("status") == "ok" else "degraded",
            "kernel": "WARDEN",
            "base_url": client.base_url,
            "health": health,
            "authoritative_for": ["probe", "sae", "causal_audit", "low_level_evidence"],
        }
    except WardenKernelError as exc:
        return {
            "status": "unreachable",
            "kernel": "WARDEN",
            "base_url": client.base_url,
            "error_code": exc.code,
            "reason": str(exc),
            "authoritative_for": ["probe", "sae", "causal_audit", "low_level_evidence"],
        }
