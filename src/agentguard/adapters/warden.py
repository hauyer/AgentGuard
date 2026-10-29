"""AgentGuard's integration boundary to the authoritative WARDEN audit kernel.

AgentGuard owns policy, approval, experience retrieval and user-facing orchestration.
WARDEN owns probe/SAE resources, audit-plan compilation, causal-audit execution and
low-level evidence. This module intentionally contains no model hooks or causal
inference implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 1024 * 1024
_RETRYABLE_STATUS = frozenset({408, 429, 502, 503, 504})
_RETRYABLE_ERRORS = frozenset({"WARDEN_UNREACHABLE", "WARDEN_TIMEOUT", "WARDEN_TRANSPORT_ERROR"})
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_CORE_OPERATIONS = {
    "/v1/health": {"get"},
    "/v1/capabilities": {"get"},
    "/v1/resources": {"get"},
    "/v1/contracts": {"get"},
    "/v1/audit-plans": {"get", "post"},
    "/v1/audit-plans/{plan_id}": {"get"},
    "/v1/runs": {"get", "post"},
    "/v1/runs/{run_id}": {"get"},
}


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
        research_token: str | None = None,
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
        if research_token is not None and (
            not research_token.strip() or any(ord(char) < 32 for char in research_token)
        ):
            raise ValueError("WARDEN research token is invalid")
        self._research_token = research_token.strip() if research_token else None
        self._api_version: str | None = None
        self._health_ok: bool | None = None
        self._openapi_summary_cache: dict[str, Any] | None = None
        self._openapi_summary_cached_at = 0.0

    @classmethod
    def from_env(cls, *, transport: Transport | None = None) -> WardenKernelClient | None:
        """Return None when WARDEN is not configured; never silently start a local kernel."""
        base_url = os.environ.get("AGENTGUARD_WARDEN_URL", "").strip()
        if not base_url:
            return None
        try:
            timeout = float(os.environ.get("AGENTGUARD_WARDEN_TIMEOUT", "15"))
            retries = int(os.environ.get("AGENTGUARD_WARDEN_READ_RETRIES", "0"))
            return cls(
                base_url, timeout=timeout, transport=transport, read_retries=retries,
                research_token=os.environ.get("AGENTGUARD_WARDEN_RESEARCH_TOKEN"),
            )
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
        if not isinstance(plan.get("plan_id"), str) or not plan["plan_id"].strip():
            raise WardenKernelError(
                f"WARDEN {endpoint} response is missing plan_id", code="WARDEN_BAD_RESPONSE"
            )
        if not isinstance(plan.get("status"), str) or not plan["status"].strip():
            raise WardenKernelError(
                f"WARDEN {endpoint} response has an invalid status", code="WARDEN_BAD_RESPONSE"
            )
        if "blockers" in plan and (
            not isinstance(plan["blockers"], list)
            or any(not isinstance(item, str) for item in plan["blockers"])
        ):
            raise WardenKernelError(
                f"WARDEN {endpoint} response has invalid blockers", code="WARDEN_BAD_RESPONSE"
            )
        if "request" in plan and not isinstance(plan["request"], dict):
            raise WardenKernelError(
                f"WARDEN {endpoint} response has an invalid request", code="WARDEN_BAD_RESPONSE"
            )
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

    def openapi_summary(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Read and validate the live OpenAPI contract without exposing its full body."""
        now = time.monotonic()
        if (
            self._openapi_summary_cache is not None
            and not force_refresh
            and now - self._openapi_summary_cached_at <= 5.0
        ):
            return dict(self._openapi_summary_cache)

        schema = self._expect_object(self._request("GET", "/openapi.json"), "/openapi.json")
        openapi_version = schema.get("openapi")
        info = schema.get("info")
        raw_paths = schema.get("paths")
        components = schema.get("components")
        component_schemas = components.get("schemas") if isinstance(components, dict) else None
        if (
            not isinstance(openapi_version, str)
            or not openapi_version.startswith("3.")
            or not isinstance(info, dict)
            or info.get("title") != "WARDEN API"
            or not isinstance(info.get("version"), str)
            or not info["version"].strip()
            or not isinstance(raw_paths, dict)
            or not isinstance(component_schemas, dict)
        ):
            raise WardenKernelError(
                "WARDEN OpenAPI document has an incompatible shape", code="WARDEN_BAD_OPENAPI"
            )

        path_methods: dict[str, dict[str, dict[str, Any]]] = {}
        for path, operations in raw_paths.items():
            if not isinstance(path, str) or not isinstance(operations, dict):
                raise WardenKernelError(
                    "WARDEN OpenAPI paths have an incompatible shape", code="WARDEN_BAD_OPENAPI"
                )
            parsed_operations: dict[str, dict[str, Any]] = {}
            for method, operation in operations.items():
                if str(method).lower() not in _HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    raise WardenKernelError(
                        "WARDEN OpenAPI operation has an incompatible shape",
                        code="WARDEN_BAD_OPENAPI",
                    )
                parsed_operations[str(method).lower()] = operation
            path_methods[path] = parsed_operations

        missing: list[str] = []
        for path, methods in _CORE_OPERATIONS.items():
            operations = path_methods.get(path, {})
            for method in sorted(methods):
                operation = operations.get(method)
                if operation is None:
                    missing.append(f"{method.upper()} {path}")
                    continue
                responses = operation.get("responses")
                if not isinstance(responses, dict) or not any(
                    str(code).startswith("2") for code in responses
                ):
                    missing.append(f"{method.upper()} {path} success response")

        for path, method, schema_name in (
            ("/v1/audit-plans", "post", "AuditRequestSchema"),
            ("/v1/runs", "post", "RunRequestSchema"),
        ):
            operation = path_methods.get(path, {}).get(method, {})
            body = operation.get("requestBody")
            content = body.get("content") if isinstance(body, dict) else None
            json_content = content.get("application/json") if isinstance(content, dict) else None
            request_schema = json_content.get("schema") if isinstance(json_content, dict) else None
            expected_ref = f"#/components/schemas/{schema_name}"
            if (
                not isinstance(request_schema, dict)
                or request_schema.get("$ref") != expected_ref
                or schema_name not in component_schemas
            ):
                missing.append(f"{method.upper()} {path} request schema {schema_name}")

        audit_schema = component_schemas.get("AuditRequestSchema")
        audit_properties = audit_schema.get("properties") if isinstance(audit_schema, dict) else None
        audit_required = audit_schema.get("required", []) if isinstance(audit_schema, dict) else []
        if (
            not isinstance(audit_properties, dict)
            or not {"request_id", "contract_id", "model_id", "runtime_id"}.issubset(audit_required)
            or any(
                not isinstance(audit_properties.get(field), dict)
                or audit_properties[field].get("type") != "string"
                for field in ("request_id", "contract_id", "model_id", "runtime_id")
            )
        ):
            missing.append("POST /v1/audit-plans request schema required fields")
        else:
            mode_schema = audit_properties.get("mode")
            if isinstance(mode_schema, dict) and isinstance(mode_schema.get("$ref"), str):
                prefix = "#/components/schemas/"
                ref_name = mode_schema["$ref"].removeprefix(prefix)
                mode_schema = component_schemas.get(ref_name) if mode_schema["$ref"].startswith(prefix) else None
            expected_modes = {"capture_only", "detection", "causal_audit", "defense_evaluation"}
            if (
                not isinstance(mode_schema, dict)
                or set(mode_schema.get("enum", [])) != expected_modes
            ):
                missing.append("POST /v1/audit-plans request schema WARDEN AuditMode enum")
            capture_schema = audit_properties.get("capture_targets")
            capture_items = capture_schema.get("items") if isinstance(capture_schema, dict) else None
            if (
                not isinstance(capture_items, dict)
                or capture_items.get("$ref") != "#/components/schemas/CaptureTargetSchema"
                or not isinstance(component_schemas.get("CaptureTargetSchema"), dict)
            ):
                missing.append("POST /v1/audit-plans request schema CaptureTargetSchema")

        run_schema = component_schemas.get("RunRequestSchema")
        run_properties = run_schema.get("properties") if isinstance(run_schema, dict) else None
        run_required = run_schema.get("required", []) if isinstance(run_schema, dict) else []
        if (
            not isinstance(run_properties, dict)
            or "plan_id" not in run_required
            or not isinstance(run_properties.get("plan_id"), dict)
            or run_properties["plan_id"].get("type") != "string"
        ):
            missing.append("POST /v1/runs request schema required plan_id")

        feature_state: dict[str, str] = {}
        for name, markers in (
            ("whitebox_workflow_api_available", ("whitebox", "probe")),
            ("intervention_api_available", ("intervention", "defense")),
            ("artifact_access_available", ("artifact",)),
        ):
            matching = [path for path in path_methods if any(
                marker in path.lower() for marker in markers
            )]
            # A matching path is only a candidate; until AgentGuard has a reviewed,
            # versioned schema for it, path presence alone cannot enable execution.
            feature_state[name] = "unsupported" if not matching else "unknown"

        audit_schema = component_schemas.get("AuditRequestSchema")
        audit_properties = audit_schema.get("properties") if isinstance(audit_schema, dict) else None
        research_fields_available = isinstance(audit_properties, dict) and {
            "research_protocol_ref", "study_arm_ref",
        }.issubset(audit_properties)
        artifact_path = "/v1/artifacts/{artifact_id}/content"
        artifact_operation = path_methods.get(artifact_path, {}).get("get")
        artifact_responses = artifact_operation.get("responses") if isinstance(artifact_operation, dict) else None
        artifact_media_types: set[str] = set()
        if isinstance(artifact_responses, dict):
            for response in artifact_responses.values():
                content = response.get("content") if isinstance(response, dict) else None
                if isinstance(content, dict):
                    artifact_media_types.update(str(value) for value in content)

        canonical = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        summary = {
            "openapi_version": openapi_version,
            "service_version": info["version"],
            "path_count": len(path_methods),
            "schema_sha256": hashlib.sha256(canonical).hexdigest(),
            "path_methods": {
                path: sorted(methods) for path, methods in sorted(path_methods.items())
            },
            "missing_contract_items": missing,
            "compatible": not missing,
            "research_request_fields_available": research_fields_available,
            "artifact_content_operation_available": isinstance(artifact_operation, dict),
            "artifact_content_media_types": sorted(artifact_media_types),
            "artifact_content_media_type_compatible": "application/octet-stream" in artifact_media_types,
            **feature_state,
        }
        self._openapi_summary_cache = summary
        self._openapi_summary_cached_at = now
        return dict(summary)

    def _require_public_contract(self) -> dict[str, Any]:
        self._require_compatible_api()
        summary = self.openapi_summary()
        if not summary["compatible"]:
            raise WardenKernelError(
                "WARDEN OpenAPI does not match the supported v1 contract",
                code="WARDEN_API_CONTRACT_INCOMPATIBLE",
            )
        return summary

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

    def execution_contract(self, protocol_ref: str, arm_id: str) -> dict[str,Any]:
        """Read the authoritative frozen target without creating a plan or model job."""
        path=f"/v1/research-protocols/{quote(protocol_ref,safe='')}/arms/{quote(arm_id,safe='')}"
        value=self._expect_object(self._request("GET",path),path)
        if (value.get("schema_version")!="warden.execution-contract/1"
                or value.get("protocol_ref")!=protocol_ref or value.get("arm_id")!=arm_id):
            raise WardenKernelError("WARDEN frozen contract identity mismatch",code="WARDEN_BAD_RESPONSE")
        return value

    def list_audit_plans(self) -> list[dict[str, Any]]:
        """List plans visible to the current WARDEN process for read-only reconciliation."""
        values = self._expect_object_list(self._request("GET", "/v1/audit-plans"), "/v1/audit-plans")
        return [self._public_plan(value, "/v1/audit-plans") for value in values]

    def create_audit_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from agentguard.contracts.warden import WardenAuditRequest

        self._require_public_contract()
        try:
            validated_payload = WardenAuditRequest.model_validate(payload).to_warden_payload()
        except (TypeError, ValueError):
            raise WardenKernelError(
                "AgentGuard audit request does not match the WARDEN v1 schema",
                code="WARDEN_BAD_REQUEST",
            ) from None
        research = bool(validated_payload.get("research_protocol_ref") or validated_payload.get("study_arm_ref"))
        headers = None
        if research:
            summary = self.openapi_summary()
            if summary.get("research_request_fields_available") is not True:
                raise WardenKernelError(
                    "WARDEN does not publish the paired research reference fields",
                    code="WARDEN_RESEARCH_CONTRACT_UNAVAILABLE",
                )
            headers = self._research_headers()
        return self._public_plan(
            self._request("POST", "/v1/audit-plans", validated_payload, extra_headers=headers),
            "/v1/audit-plans",
        )

    def get_audit_plan(self, plan_id: str) -> dict[str, Any]:
        path = f"/v1/audit-plans/{quote(plan_id, safe='')}"
        return self._public_plan(self._request("GET", path), path)

    def start_run(self, plan_id: str, *, research: bool = False) -> dict[str, Any]:
        self._require_public_contract()
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise WardenKernelError("WARDEN run request is missing plan_id", code="WARDEN_BAD_REQUEST")
        headers = None
        if research:
            if self.openapi_summary().get("research_request_fields_available") is not True:
                raise WardenKernelError(
                    "WARDEN does not publish the paired research reference fields",
                    code="WARDEN_RESEARCH_CONTRACT_UNAVAILABLE",
                )
            headers = self._research_headers()
        return self._expect_run(
            self._request("POST", "/v1/runs", {"plan_id": plan_id}, extra_headers=headers),
            "/v1/runs",
        )

    @property
    def research_token_configured(self) -> bool:
        return self._research_token is not None

    def _research_headers(self) -> dict[str, str]:
        if self._research_token is None:
            raise WardenKernelError(
                "WARDEN research token is not configured", code="WARDEN_RESEARCH_TOKEN_MISSING",
            )
        return {"Authorization": f"Bearer {self._research_token}"}

    def read_public_artifact(
        self, artifact_id: str, expected_sha256: str, *, max_bytes: int = _MAX_ARTIFACT_BYTES,
    ) -> bytes:
        """Read one bounded public-summary artifact and verify its content digest.

        This remains fail-closed while the published WARDEN artifact response
        media type disagrees with the runtime contract.
        """
        if not isinstance(artifact_id, str) or not re.fullmatch(
            r"public-summary-[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", artifact_id,
        ):
            raise WardenKernelError("WARDEN artifact ID is not an approved public summary", code="WARDEN_ARTIFACT_NOT_PUBLIC")
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise WardenKernelError("Expected artifact SHA-256 is invalid", code="WARDEN_BAD_REQUEST")
        if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_ARTIFACT_BYTES:
            raise WardenKernelError("Artifact byte limit must be between 1 and 1048576", code="WARDEN_BAD_REQUEST")
        summary = self._require_public_contract()
        if summary.get("artifact_content_operation_available") is not True:
            raise WardenKernelError("WARDEN artifact content operation is unavailable", code="WARDEN_ARTIFACT_READ_UNAVAILABLE")
        if summary.get("artifact_content_media_type_compatible") is not True:
            raise WardenKernelError(
                "WARDEN artifact response media type does not match the binary reader contract",
                code="WARDEN_ARTIFACT_MEDIA_TYPE_MISMATCH",
            )
        headers = self._research_headers()
        path = f"/v1/artifacts/{quote(artifact_id, safe='')}/content"
        raw = self._request(
            "GET", path, extra_headers=headers, accept="application/octet-stream",
            parse_json=False, max_response_bytes=max_bytes,
        )
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise WardenKernelError("WARDEN artifact content SHA-256 does not match", code="WARDEN_ARTIFACT_HASH_MISMATCH")
        return raw

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = f"/v1/runs/{quote(run_id, safe='')}"
        return self._expect_run(self._request("GET", path), path)

    def list_runs(self) -> list[dict[str, Any]]:
        """List WARDEN runs for safe reconciliation of an uncertain submission."""
        values = self._expect_object_list(self._request("GET", "/v1/runs"), "/v1/runs")
        return [self._expect_run(value, "/v1/runs") for value in values]

    @classmethod
    def _expect_run(cls, value: Any, endpoint: str) -> dict[str, Any]:
        run = cls._expect_object(value, endpoint)
        if not isinstance(run.get("run_id"), str) or not run["run_id"].strip():
            raise WardenKernelError(
                f"WARDEN {endpoint} response is missing run_id", code="WARDEN_BAD_RESPONSE"
            )
        if not isinstance(run.get("status"), str) or not run["status"].strip():
            raise WardenKernelError(
                f"WARDEN {endpoint} response has an invalid status", code="WARDEN_BAD_RESPONSE"
            )
        if "plan_id" in run and (
            not isinstance(run["plan_id"], str) or not run["plan_id"].strip()
        ):
            raise WardenKernelError(
                f"WARDEN {endpoint} response has an invalid plan_id", code="WARDEN_BAD_RESPONSE"
            )
        return run

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
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        extra_headers: Mapping[str, str] | None = None,
        accept: str = "application/json",
        parse_json: bool = True,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> Any:
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        attempts = self.read_retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                status, raw = self._transport(
                    method, self.base_url + path,
                    {
                        "Accept": accept, "Content-Type": "application/json",
                        **dict(extra_headers or {}),
                    },
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
        if len(raw) > max_response_bytes:
            raise WardenKernelError("WARDEN response exceeds size limit", code="WARDEN_BAD_RESPONSE")
        if not parse_json:
            return raw
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
