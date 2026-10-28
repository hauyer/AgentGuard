"""Project real WARDEN diagnostics into explicit research capability states."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentguard.contracts.research_capability import (
    ResearchCapabilityItem,
    ResearchCapabilityReport,
)

_CANDIDATES = (
    "research_dataset_mechanism",
    "sender_receiver_baseline",
    "agent_isolation",
    "message_block",
    "message_replace",
    "leace_fit_apply",
    "activation_patch",
    "residual_edge_patch",
    "probe_training",
    "persistent_run",
    "artifact_content_read",
)
_UNKNOWN_VERSIONED = frozenset({"WARDEN_API_CONTRACT_INCOMPATIBLE", "WARDEN_API_UNVERSIONED"})


def build_research_capability_report(diagnostic: dict[str, Any]) -> ResearchCapabilityReport:
    """Project exact public operations into fail-closed research readiness."""
    observed = datetime.now(UTC).isoformat()
    summary = diagnostic.get("openapi_summary")
    summary = summary if isinstance(summary, dict) else {}
    source_version = diagnostic.get("kernel_version") or summary.get("service_version")
    source_version = source_version if isinstance(source_version, str) else None
    api_version = diagnostic.get("api_version")
    api_version = api_version if isinstance(api_version, str) else None
    backend_status = diagnostic.get("status", "unknown")
    if backend_status == "ready":
        backend_status = "available"
    if backend_status not in {"available", "unavailable", "blocked", "unknown", "not_configured"}:
        backend_status = "unknown"

    advertised: set[str] = set()
    raw_capabilities = diagnostic.get("capabilities")
    if isinstance(raw_capabilities, list):
        for component in raw_capabilities:
            if not isinstance(component, dict):
                continue
            values = component.get("capabilities")
            if isinstance(values, list):
                advertised.update(value for value in values if isinstance(value, str))

    diagnostic_blockers = diagnostic.get("blockers")
    diagnostic_codes = {
        code for code in diagnostic_blockers
        if isinstance(code, str) and code.isascii() and len(code) <= 128
    } if isinstance(diagnostic_blockers, list) else set()
    compatible = summary.get("compatible") is True and api_version == "v1"
    path_methods = summary.get("path_methods")
    path_methods = path_methods if isinstance(path_methods, dict) else {}
    research_fields = summary.get("research_request_fields_available") is True
    artifact_read = summary.get("artifact_content_operation_available") is True
    artifact_media_ok = summary.get("artifact_content_media_type_compatible") is True
    token_configured = diagnostic.get("research_token_configured") is True
    capability_statuses = diagnostic.get("capability_statuses")
    capability_statuses = capability_statuses if isinstance(capability_statuses, dict) else {}

    items: list[ResearchCapabilityItem] = []
    for name in _CANDIDATES:
        is_advertised = name in advertised if isinstance(raw_capabilities, list) else None
        if backend_status == "not_configured":
            status = "UNAVAILABLE"
            blockers = ["WARDEN_URL_NOT_CONFIGURED"]
        elif backend_status == "unavailable":
            status = "UNAVAILABLE"
            blockers = sorted(diagnostic_codes) or ["WARDEN_SERVICE_UNAVAILABLE"]
        elif backend_status == "unknown":
            status = "UNKNOWN"
            blockers = ["WARDEN_CAPABILITY_RESPONSE_UNAVAILABLE"]
        elif not compatible:
            status = "UNKNOWN" if diagnostic_codes & _UNKNOWN_VERSIONED else "SUPPORTED_BUT_BLOCKED"
            blockers = ["WARDEN_API_CONTRACT_NOT_VERIFIED"]
        elif name == "research_dataset_mechanism" and research_fields:
            status = "SUPPORTED_BUT_BLOCKED"
            blockers = ["WARDEN_RESEARCH_STAGE_ALLOWLIST_NOT_ADVERTISED"]
            if not token_configured:
                blockers.append("WARDEN_RESEARCH_TOKEN_MISSING")
            if diagnostic.get("runtime_available") is not True:
                blockers.append("WARDEN_RUNTIME_NOT_READY")
            if capability_statuses.get("persistent_run_available") != "available":
                blockers.append("WARDEN_DURABLE_DISPATCHER_NOT_ADVERTISED")
            blockers.append("WARDEN_LIVE_RESEARCH_SMOKE_NOT_RUN")
        elif name == "persistent_run" and "/v1/runs/{run_id}" in path_methods:
            status = "SUPPORTED_BUT_BLOCKED"
            blockers = ["WARDEN_DURABLE_DISPATCHER_NOT_ADVERTISED", "WARDEN_CROSS_RESTART_RECONCILIATION_NOT_VERIFIED"]
        elif name == "artifact_content_read" and artifact_read:
            status = "SUPPORTED_BUT_BLOCKED"
            blockers = []
            if not token_configured:
                blockers.append("WARDEN_RESEARCH_TOKEN_MISSING")
            if capability_statuses.get("artifact_reader_available") != "available":
                blockers.append("WARDEN_ARTIFACT_READER_NOT_CONFIGURED")
            if not artifact_media_ok:
                blockers.append("WARDEN_ARTIFACT_OPENAPI_MEDIA_TYPE_MISMATCH")
            blockers.append("WARDEN_LIVE_ARTIFACT_HASH_SMOKE_NOT_RUN")
        elif is_advertised:
            status = "SUPPORTED_BUT_BLOCKED"
            blockers = ["WARDEN_RESEARCH_OPERATION_UNREVIEWED"]
        else:
            status = "UNSUPPORTED"
            blockers = ["WARDEN_RESEARCH_CAPABILITY_NOT_DECLARED"]
        items.append(ResearchCapabilityItem(
            capability=name,
            status=status,
            blockers=blockers,
            source_version=source_version,
            source_api_version=api_version,
            advertised_by_warden=is_advertised,
            observed_at=observed,
            evidence_refs=[],
            contract_ref=(
                "WARDEN:POST /v1/audit-plans + POST /v1/runs"
                if name == "research_dataset_mechanism" and research_fields
                else "WARDEN:GET /v1/runs/{run_id}"
                if name == "persistent_run" and "/v1/runs/{run_id}" in path_methods
                else "WARDEN:GET /v1/artifacts/{artifact_id}/content"
                if name == "artifact_content_read" and artifact_read
                else None
            ),
        ))

    limitations = [
        "Capability states are derived from the observed WARDEN OpenAPI and capability response.",
        "A healthy WARDEN API does not establish model, GPU, Probe, intervention, or artifact readiness.",
        "The default WARDEN deployment does not advertise the durable dispatcher, research-stage allowlist, or artifact reader as configured.",
        "Artifact content remains blocked while the published media type differs from the binary response contract.",
        "No research operation is submitted by this read-only capability check.",
    ]
    return ResearchCapabilityReport(
        backend_status=backend_status,
        source_version=source_version,
        source_api_version=api_version,
        observed_at=observed,
        capabilities=items,
        limitations=limitations,
    )

