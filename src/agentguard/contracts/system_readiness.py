"""Versioned public contracts for layered readiness and investigation options (v1.0).

Every check keeps ``available`` and its evidence separate: WARDEN health is
never promoted to model-runtime, audit-execution or intervention-execution
availability. Unknown stays unknown; unsupported stays unsupported.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel

ReadinessStatus = Literal["available", "unavailable", "unknown", "unsupported", "blocked"]

AuditMode = Literal[
    "observation", "capture_only", "detection", "causal_audit", "defense_evaluation"
]


class ReadinessCheck(ContractModel):
    """One named capability layer with its own evidence and blockers."""

    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=128)
    status: ReadinessStatus
    last_checked_at: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=256)
    blockers: list[str] = Field(default_factory=list, max_length=32)
    diagnostics: dict[str, str | int | float | bool] = Field(default_factory=dict)
    capability_version: str | None = Field(default=None, max_length=128)


class ReadinessCacheInfo(ContractModel):
    """Provenance of the aggregated readiness payload."""

    schema_version: Literal["1.0"] = "1.0"
    source: Literal["live", "cache"]
    generated_at: str = Field(min_length=1)
    age_seconds: float = Field(ge=0.0)
    ttl_seconds: float = Field(gt=0.0)
    is_stale: bool
    note: str = Field(default="", max_length=512)


class SystemReadinessResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    checks: list[ReadinessCheck] = Field(min_length=1, max_length=64)
    generated_at: str = Field(min_length=1)
    cache: ReadinessCacheInfo
    limitations: list[str] = Field(default_factory=list, max_length=32)


class InvestigationModeOption(ContractModel):
    """Submission capability for one audit mode, kept distinct from planning."""

    schema_version: Literal["1.0"] = "1.0"
    mode: AuditMode
    plan_creation: ReadinessStatus
    run_execution: ReadinessStatus
    submittable: bool
    blockers: list[str] = Field(default_factory=list, max_length=32)
    note: str = Field(default="", max_length=1024)


class ResourceOption(ContractModel):
    """A Probe/SAE resource exactly as the kernel reported it."""

    schema_version: Literal["1.0"] = "1.0"
    resource_id: str = Field(min_length=1, max_length=256)
    kind: Literal["probe", "sae"]
    state: str = Field(min_length=1, max_length=64)
    version: str | None = Field(default=None, max_length=128)


class CaptureTargetSupport(ContractModel):
    """What the audit request contract accepts for capture targets."""

    schema_version: Literal["1.0"] = "1.0"
    accepted_fields: list[str] = Field(min_length=1, max_length=32)
    activation_capture_available: bool
    note: str = Field(default="", max_length=1024)


class InvestigationOptionsResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    available: bool
    error_code: str | None = Field(default=None, max_length=128)
    modes: list[InvestigationModeOption] = Field(min_length=1, max_length=16)
    threat_contracts: list[str] = Field(default_factory=list, max_length=200)
    probe_resources: list[ResourceOption] = Field(default_factory=list, max_length=200)
    sae_resources: list[ResourceOption] = Field(default_factory=list, max_length=200)
    confirmed_runtime_ids: list[str] = Field(default_factory=list, max_length=64)
    capture_targets: CaptureTargetSupport
    missing_dependencies: list[str] = Field(default_factory=list, max_length=64)
    generated_at: str = Field(min_length=1)
    cache: ReadinessCacheInfo
    limitations: list[str] = Field(default_factory=list, max_length=32)
