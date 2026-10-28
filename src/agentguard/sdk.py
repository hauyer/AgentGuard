"""Small standard-library client for the stable AgentGuard business API."""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class AgentGuardAPIError(RuntimeError):
    """A bounded API failure without response secrets or request bodies."""

    def __init__(self, status_code: int, code: str, message: str, request_id: str | None = None):
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        super().__init__(message)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener() -> Callable[..., Any]:
    return build_opener(_RejectRedirects()).open


class AgentGuardClient:
    """Call typed AgentGuard API paths; callers cannot choose arbitrary hosts per request."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        token: str | None = None,
        operator_token: str | None = None,
        timeout: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("base_url contains an invalid port") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("base_url must be an HTTP(S) origin without credentials, path, query, or fragment")
        if not 0 < timeout <= 120:
            raise ValueError("timeout must be in the range (0, 120]")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.token = token
        self.operator_token = operator_token
        self.timeout = timeout
        self._opener = opener or _default_opener()

    @classmethod
    def from_env(cls) -> AgentGuardClient:
        return cls(
            os.environ.get("AGENTGUARD_BASE_URL", "http://127.0.0.1:8000"),
            token=os.environ.get("AGENTGUARD_API_TOKEN"),
            operator_token=os.environ.get("AGENTGUARD_OPERATOR_API_TOKEN"),
            timeout=float(os.environ.get("AGENTGUARD_API_TIMEOUT", "15")),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        operator: bool = False,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/v1/") or "?" in path or "#" in path:
            raise ValueError("path must be a versioned AgentGuard /v1 path")
        if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("unsupported HTTP method")
        query_values = {key: value for key, value in (query or {}).items() if value is not None}
        url = self.base_url + path
        if query_values:
            url += "?" + urlencode(query_values)
        headers = {
            "Accept": "application/json",
            "X-Request-ID": request_id or str(uuid.uuid4()),
        }
        if self.token:
            headers["X-AgentGuard-Token"] = self.token
        if operator and self.operator_token:
            headers["X-AgentGuard-Operator-Token"] = self.operator_token
        for key, value in (extra_headers or {}).items():
            if value:
                headers[key] = value
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                raw = response.read(4 * 1024 * 1024 + 1)
                status = getattr(response, "status", 200)
                response_request_id = response.headers.get("X-Request-ID")
        except HTTPError as exc:
            raw = exc.read(64 * 1024)
            status = exc.code
            response_request_id = exc.headers.get("X-Request-ID") if exc.headers else None
        except (URLError, TimeoutError, OSError) as exc:
            raise AgentGuardAPIError(0, "API_UNREACHABLE", f"AgentGuard API request failed: {type(exc).__name__}") from exc
        if len(raw) > 4 * 1024 * 1024:
            raise AgentGuardAPIError(status, "API_RESPONSE_TOO_LARGE", "AgentGuard API response exceeded 4 MiB", response_request_id)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentGuardAPIError(status, "API_BAD_RESPONSE", "AgentGuard API returned invalid JSON", response_request_id) from exc
        if not isinstance(payload, dict):
            raise AgentGuardAPIError(status, "API_BAD_RESPONSE", "AgentGuard API response must be a JSON object", response_request_id)
        if not 200 <= status < 300:
            detail = payload.get("detail", {})
            if not isinstance(detail, dict):
                detail = {"message": "AgentGuard API request was rejected"}
            raise AgentGuardAPIError(
                status,
                str(detail.get("error", "API_REQUEST_REJECTED")),
                str(detail.get("message", detail.get("error", "AgentGuard API request was rejected"))),
                response_request_id or detail.get("request_id"),
            )
        return payload

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/v1/system/health")

    def capabilities(self) -> dict[str, Any]:
        return self.request("GET", "/v1/system/capabilities")

    def kernel_status(self) -> dict[str, Any]:
        return self.request("GET", "/v1/kernel/status")

    def list_scenarios(self) -> dict[str, Any]:
        return self.request("GET", "/v1/scenarios")

    def create_investigation(self, payload: Mapping[str, Any], *, request_id: str | None = None) -> dict[str, Any]:
        return self.request("POST", "/v1/investigations", body=payload, request_id=request_id)

    def list_investigations(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/investigations", query={"status": status, "limit": limit, "offset": offset}
        )

    def get_investigation(self, request_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/investigations/{quote(request_id, safe='')}")

    def get_evidence(self, request_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/evidence/investigations/{quote(request_id, safe='')}")

    def preview_defense_policies(self, request_id: str) -> dict[str, Any]:
        return self.request("POST", f"/v1/defense-policies/{quote(request_id, safe='')}/preview")

    def get_defense_policies(self, request_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/defense-policies/{quote(request_id, safe='')}")

    def get_report(self, run_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/reports/{quote(run_id, safe='')}")

    def list_reports(
        self, *, status: str | None = None, scenario_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/reports",
            query={"status": status, "scenario_id": scenario_id,
                   "limit": limit, "offset": offset},
        )

    def get_readiness(self, *, force_refresh: bool = False) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/system/readiness",
            query={"force_refresh": str(force_refresh).lower()} if force_refresh else None,
        )

    def get_investigation_options(self, *, force_refresh: bool = False) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/investigations/options",
            query={"force_refresh": str(force_refresh).lower()} if force_refresh else None,
        )

    def get_investigation_graph(self, request_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/investigations/{quote(request_id, safe='')}/interaction-graph",
            query={"run_id": run_id},
        )

    def get_investigation_timeline(
        self, request_id: str, *, after_event_id: int | None = None
    ) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/investigations/{quote(request_id, safe='')}/timeline",
            query={"after_event_id": after_event_id},
        )

    def list_experiments(
        self, *, status: str | None = None, scenario_id: str | None = None,
        data_source: str | None = None, limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/experiments",
            query={"status": status, "scenario_id": scenario_id,
                   "data_source": data_source, "limit": limit, "offset": offset},
        )

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/experiments/{quote(experiment_id, safe='')}")

    def compare_experiments(self, run_ids: list[str]) -> dict[str, Any]:
        return self.request("POST", "/v1/experiments/compare", body={"run_ids": run_ids})

    def search_verified_experiences(
        self, *, query: str = "", scenario_id: str | None = None, risk_type: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/experiences",
            query={"q": query, "scenario_id": scenario_id, "risk_type": risk_type,
                   "verified_only": "true", "limit": limit},
        )

    def get_api_audit_events(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/system/audit-events", query={"limit": limit, "offset": offset}, operator=True,
        )

    def get_risk_analysis(self, request_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/investigations/{quote(request_id, safe='')}/risk-analysis",
            query={"run_id": run_id},
        )

    def create_investigation_loop(
        self, payload: Mapping[str, Any], *,
        idempotency_key: str | None = None,
        operator: bool = True,
        approval_token: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        extra: dict[str, str] = {}
        if approval_token:
            extra["X-Approval-Token"] = approval_token
        if idempotency_key:
            extra["X-Loop-Idempotency-Key"] = idempotency_key
        return self.request(
            "POST", "/v1/investigation-loops", body=payload,
            request_id=request_id, operator=operator, extra_headers=extra,
        )

    def list_investigation_loops(
        self, *, base_request_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/investigation-loops",
            query={"base_request_id": base_request_id,
                   "limit": limit, "offset": offset},
        )

    def get_investigation_loop(self, loop_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/investigation-loops/{quote(loop_id, safe='')}",
        )

    def list_investigation_decisions(self, loop_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/investigation-loops/{quote(loop_id, safe='')}/decisions",
        )

    def get_investigation_evidence_changes(self, loop_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/v1/investigation-loops/{quote(loop_id, safe='')}/evidence-changes",
        )

    def get_investigation_loop_budget(self, loop_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/investigation-loops/{quote(loop_id, safe='')}/budget",
        )

    def list_strategy_evaluations(
        self,
        *,
        data_partition: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if data_partition is not None:
            params["data_partition"] = data_partition
        return self.request("GET", "/v1/research/strategy-evaluations", query=params)

    def get_strategy_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/research/strategy-evaluations/{quote(evaluation_id, safe='')}",
        )

    def get_research_capabilities(self) -> dict[str, Any]:
        """Read the WARDEN research capability gate; this never submits work."""
        return self.request("GET", "/v1/research/capabilities")

    def list_studies(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        track: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "GET", "/v1/studies",
            query={"limit": limit, "offset": offset, "track": track,
                   "sort_by": sort_by, "sort_order": sort_order},
        )

    def get_study(self, study_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/studies/{quote(study_id, safe='')}")

    def register_study(
        self, payload: Mapping[str, Any], *, approval_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"X-Approval-Token": approval_token} if approval_token else None
        headers = {**(headers or {}), **({"Idempotency-Key": idempotency_key} if idempotency_key else {})}
        return self.request("POST", "/v1/studies", body=payload, operator=True, extra_headers=headers)

    def analyze_study(
        self, study_id: str, payload: Mapping[str, Any], *, approval_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"X-Approval-Token": approval_token} if approval_token else None
        headers = {**(headers or {}), **({"Idempotency-Key": idempotency_key} if idempotency_key else {})}
        return self.request(
            "POST", f"/v1/studies/{quote(study_id, safe='')}/analyses",
            body=payload, operator=True, extra_headers=headers,
        )

    def list_study_analyses(
        self, study_id: str, *, limit: int | None = None, offset: int | None = None,
        evidence_mode: str | None = None, sort_order: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "GET", f"/v1/studies/{quote(study_id, safe='')}/analyses",
            query={"limit": limit, "offset": offset,
                   "evidence_mode": evidence_mode, "sort_order": sort_order},
        )

    def activate_experience_policy(self, payload: Mapping[str, Any], *, activate: bool = True) -> dict[str, Any]:
        return self.request(
            "POST", "/v1/experiences/recommendation-policies", query={"activate": str(activate).lower()},
            body=payload, operator=True,
        )

    def rollback_experience_policy(self, policy_version: str, *, reason: str) -> dict[str, Any]:
        return self.request(
            "POST", f"/v1/experiences/recommendation-policies/{quote(policy_version, safe='')}/rollback",
            query={"reason": reason}, operator=True,
        )
