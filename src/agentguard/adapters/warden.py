"""AgentGuard's integration boundary to the authoritative WARDEN audit kernel.

AgentGuard owns policy, approval, experience retrieval and user-facing orchestration.
WARDEN owns probe/SAE resources, audit-plan compilation, causal-audit execution and
low-level evidence. This module intentionally contains no model hooks or causal
inference implementation.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_RETRYABLE_STATUS = frozenset({408, 429, 502, 503, 504})
_RETRYABLE_ERRORS = frozenset({"WARDEN_UNREACHABLE", "WARDEN_TIMEOUT", "WARDEN_TRANSPORT_ERROR"})


class WardenKernelError(RuntimeError):
    """Sanitized failure raised at the AgentGuard/WARDEN boundary."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str = "WARDEN_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WardenKernelError("WARDEN redirect rejected", code="WARDEN_REDIRECT_REJECTED")


def _read_bounded(response: Any) -> bytes:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise WardenKernelError("WARDEN response exceeds size limit", code="WARDEN_BAD_RESPONSE")
    return raw


def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = Request(url, data=body, method=method, headers=dict(headers))
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            return int(response.status), _read_bounded(response)
    except HTTPError as exc:
        with exc:
            return int(exc.code), b""
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise WardenKernelError("WARDEN request timed out", code="WARDEN_TIMEOUT") from None
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
        read_retries: int = 0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            any(ord(char) < 32 for char in base_url)
            or " " in base_url.strip() or "\\" in base_url
        ):
            raise ValueError("WARDEN base_url contains invalid characters")
        normalized = base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        local_http = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local_http):
            raise ValueError("WARDEN base_url must use HTTPS; plain HTTP is allowed only for loopback")
        if not parsed.netloc or not parsed.hostname:
            raise ValueError("WARDEN base_url must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("WARDEN credentials must not be embedded in base_url")
        if parsed.query or parsed.fragment or "?" in normalized or "#" in normalized:
            raise ValueError("WARDEN base_url must not contain a query or fragment")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("WARDEN base_url has an invalid port")
        if any(part in {".", ".."} for part in unquote(parsed.path).split("/")):
            raise ValueError("WARDEN base_url must not contain relative path segments")
        if not 1 <= float(timeout) <= 120:
            raise ValueError("WARDEN timeout must be between 1 and 120 seconds")
        self.base_url = normalized
        self.timeout = float(timeout)
        self._transport = transport or _urllib_transport
        if type(read_retries) is not int or not 0 <= read_retries <= 3:
            raise ValueError("WARDEN read_retries must be an integer between 0 and 3")
        self.read_retries = read_retries
        self._sleep = sleep
        self._api_version: str | None = None
        self._health_ok: bool | None = None

    @classmethod
    def from_env(cls, *, transport: Transport | None = None) -> WardenKernelClient | None:
        """Return None when WARDEN is not configured; never silently start a local kernel."""
        base_url = os.environ.get("AGENTGUARD_WARDEN_URL", "").strip()
        if not base_url:
            return None
        try:
            timeout = float(os.environ.get("AGENTGUARD_WARDEN_TIMEOUT", "15"))
            retries = int(os.environ.get("AGENTGUARD_WARDEN_READ_RETRIES", "0"))
            return cls(base_url, timeout=timeout, transport=transport, read_retries=retries)
        except ValueError:
            raise WardenKernelError(
                "Invalid WARDEN connection configuration",
                code="WARDEN_CONFIGURATION_ERROR",
            ) from None

    @staticmethod
    def _expect_object(value: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise WardenKernelError(
                f"WARDEN {endpoint} response is not an object", code="WARDEN_BAD_RESPONSE"
            )
        return value

    @classmethod
    def _public_plan(cls, value: Any, endpoint: str) -> dict[str, Any]:
        """Discard benchmark truth even when connected to an older WARDEN service."""
        plan = cls._expect_object(value, endpoint)
        contract = plan.get("threat_contract")
        if isinstance(contract, dict):
            plan = {**plan, "threat_contract": {
                key: contract[key] for key in ("contract_id", "scenario_id") if key in contract
            }}
        return plan

    def health(self) -> dict[str, Any]:
        value = self._expect_object(self._request("GET", "/v1/health"), "/v1/health")
        if any(not isinstance(value.get(key), str) or not value[key].strip()
               for key in ("status", "version", "api_version")):
            raise WardenKernelError("WARDEN health fields are invalid", code="WARDEN_BAD_RESPONSE")
        self._api_version = value["api_version"]
        self._health_ok = value["status"] == "ok"
        return value

    def _require_compatible_api(self) -> None:
        if self._api_version is None:
            self.health()
        if self._api_version != "v1":
            raise WardenKernelError(
                "WARDEN API version is incompatible", code="WARDEN_API_INCOMPATIBLE",
            )
        if self._health_ok is not True:
            raise WardenKernelError("WARDEN health is not ok", code="WARDEN_HEALTH_NOT_OK")

    @staticmethod
    def _expect_object_list(value: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise WardenKernelError(
                f"WARDEN {endpoint} response is not a list of objects", code="WARDEN_BAD_RESPONSE"
            )
        return value

    def capabilities(self) -> list[dict[str, Any]]:
        value = self._request("GET", "/v1/capabilities")
        reports = self._expect_object_list(value, "/v1/capabilities")
        identifiers = []
        for report in reports:
            identifier = report.get("component_id")
            capabilities = report.get("capabilities", [])
            if (
                not isinstance(identifier, str) or not identifier.strip()
                or type(report.get("available")) is not bool
                or not isinstance(capabilities, list)
                or any(not isinstance(item, str) or not item.strip() for item in capabilities)
            ):
                raise WardenKernelError("WARDEN capability fields are invalid", code="WARDEN_BAD_RESPONSE")
            identifiers.append(identifier)
        if len(identifiers) != len(set(identifiers)):
            raise WardenKernelError("WARDEN capability IDs are duplicated", code="WARDEN_BAD_RESPONSE")
        return reports

    def resources(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind not in {None, "probe", "sae"}:
            raise ValueError("resource kind must be 'probe', 'sae', or None")
        suffix = "" if kind is None else "?" + urlencode({"kind": kind})
        value = self._request("GET", "/v1/resources" + suffix)
        resources = self._expect_object_list(value, "/v1/resources")
        identifiers = []
        for resource in resources:
            identifier = resource.get("resource_id")
            if (
                not isinstance(identifier, str) or not identifier.strip()
                or resource.get("kind") not in {"probe", "sae"}
                or resource.get("state") not in {"declared", "ready", "unavailable", "invalid"}
                or (kind is not None and resource["kind"] != kind)
            ):
                raise WardenKernelError("WARDEN resource fields are invalid", code="WARDEN_BAD_RESPONSE")
            identifiers.append((resource["kind"], identifier))
        if len(identifiers) != len(set(identifiers)):
            raise WardenKernelError("WARDEN resource IDs are duplicated", code="WARDEN_BAD_RESPONSE")
        return resources

    def contracts(self) -> list[str]:
        value = self._request("GET", "/v1/contracts")
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(value) != len(set(value))
        ):
            raise WardenKernelError(
                "WARDEN contracts response is not a list of strings", code="WARDEN_BAD_RESPONSE"
            )
        return value

    def create_audit_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_compatible_api()
        return self._public_plan(
            self._request("POST", "/v1/audit-plans", dict(payload)), "/v1/audit-plans"
        )

    def get_audit_plan(self, plan_id: str) -> dict[str, Any]:
        path = f"/v1/audit-plans/{quote(plan_id, safe='')}"
        return self._public_plan(self._request("GET", path), path)

    def start_run(self, plan_id: str) -> dict[str, Any]:
        self._require_compatible_api()
        return self._expect_object(self._request("POST", "/v1/runs", {"plan_id": plan_id}), "/v1/runs")

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = f"/v1/runs/{quote(run_id, safe='')}"
        return self._expect_object(self._request("GET", path), path)

    def list_runs(self) -> list[dict[str, Any]]:
        """List WARDEN runs for safe reconciliation of an uncertain submission."""
        return self._expect_object_list(self._request("GET", "/v1/runs"), "/v1/runs")

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
        attempts = self.read_retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                status, raw = self._transport(
                    method, self.base_url + path,
                    {"Accept": "application/json", "Content-Type": "application/json"},
                    body, self.timeout,
                )
            except WardenKernelError as exc:
                if attempt + 1 == attempts or exc.code not in _RETRYABLE_ERRORS:
                    raise
            else:
                if status not in _RETRYABLE_STATUS or attempt + 1 == attempts:
                    break
            self._sleep(0.1 * (2 ** attempt))
        if status < 200 or status >= 300:
            if 300 <= status < 400:
                code = "WARDEN_REDIRECT_REJECTED"
            elif status in {401, 403}:
                code = "WARDEN_POLICY_DENIED"
            elif status == 429:
                code = "WARDEN_RATE_LIMITED"
            elif status >= 500:
                code = "WARDEN_KERNEL_ERROR"
            else:
                code = "WARDEN_HTTP_ERROR"
            raise WardenKernelError(
                f"WARDEN API rejected request (HTTP {status})", status_code=status, code=code,
            )
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise WardenKernelError("WARDEN response exceeds size limit", code="WARDEN_BAD_RESPONSE")
        try:
            value = json.loads(
                raw.decode("utf-8"), parse_constant=_invalid_constant,
                object_pairs_hook=_unique_object,
            )
        except (UnicodeDecodeError, ValueError):
            raise WardenKernelError(
                "WARDEN returned a non-JSON response", status_code=status, code="WARDEN_BAD_RESPONSE"
            ) from None
        return value


def _invalid_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON constant")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def warden_status(client: WardenKernelClient | None = None) -> dict[str, Any]:
    """Report integration state without conflating missing infrastructure with low risk."""
    if client is None:
        try:
            client = WardenKernelClient.from_env()
        except WardenKernelError as exc:
            return {
                "status": "misconfigured",
                "kernel": "WARDEN",
                "base_url": None,
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
