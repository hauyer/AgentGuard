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
    """Build status only from observed WARDEN contract/capability responses.

    WARDEN 0.3 exposes no reviewed operation mapping for these proposed
    capability keys. Exact advertised keys therefore remain blocked until a
    versioned operation is reviewed; missing keys on a compatible contract are
    explicitly unsupported.
    """
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
        elif is_advertised:
            status = "SUPPORTED_BUT_BLOCKED"
            blockers = ["WARDEN_RESEARCH_OPERATION_CONTRACT_UNREVIEWED"]
        else:
            status = "UNSUPPORTED"
            blockers = ["WARDEN_RESEARCH_CAPABILITY_NOT_DECLARED"]
        items.append(ResearchCapabilityItem(
            capability=name,
            status=status,
            blockers=blockers,
            source_version=source_version,
            advertised_by_warden=is_advertised,
            observed_at=observed,
        ))

    limitations = [
        "Capability states are derived from the observed WARDEN OpenAPI and capability response.",
        "A healthy WARDEN API does not establish model, GPU, Probe, intervention, or artifact readiness.",
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

