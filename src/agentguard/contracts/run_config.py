"""Versioned run-configuration contract for standardized upper-layer runs (v1.0).

The configuration separates kernel-facing fields (contract/model/runtime/audit
mode/capture targets — everything WARDEN's public request schema accepts) from
AgentGuard-only study fields (budgets, data partition, provenance commit) that
must never be sent to the kernel. Observation-only runs expose no causal
intervention entry by construction.
"""
from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agentguard.contracts.base import ContractModel
from agentguard.contracts.truth_guard import find_hidden_truth_fields
from agentguard.contracts.warden import WardenCaptureTarget

AuditMode = Literal[
    "observation", "capture_only", "detection", "causal_audit", "defense_evaluation"
]


class InvestigationRunConfig(ContractModel):
    """A repeatable, versioned configuration for one standardized investigation."""

    schema_version: Literal["1.0"] = "1.0"
    run_config_id: str = Field(
        default_factory=lambda: f"run-config-{uuid4().hex[:12]}",
        max_length=256,
    )
    scenario_id: str | None = Field(default=None, max_length=256)
    contract_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=512)
    runtime_id: str = Field(min_length=1, max_length=256)
    audit_mode: AuditMode = "observation"
    capture_targets: list[WardenCaptureTarget] = Field(default_factory=list, max_length=128)
    random_seed: int | None = None
    # AgentGuard-only study fields; never forwarded to WARDEN.
    investigation_budget: int = Field(default=0, ge=0, le=100)
    time_budget: float = Field(default=110.0, gt=0.0, le=86400.0)
    model_call_budget: int | None = Field(default=None, ge=1, le=100000)
    data_partition: Literal["training", "validation", "test", "held_out_family"] = "held_out_family"
    source_commit: str | None = Field(default=None, max_length=64)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_hidden_truth_metadata(cls, value):
        present = find_hidden_truth_fields(value)
        if present:
            raise ValueError(f"run config metadata must not contain hidden evaluator fields: {sorted(present)}")
        return value

    @model_validator(mode="after")
    def seed_required_for_repeatable_runs(self):
        if self.data_partition in {"training", "test"} and self.random_seed is None:
            raise ValueError("training/test partition runs must declare random_seed")
        return self


class ObservationRunRecord(ContractModel):
    """Outcome of executing one standardized run configuration."""

    schema_version: Literal["1.0"] = "1.0"
    run_config_id: str = Field(max_length=256)
    request_id: str = Field(max_length=256)
    plan_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    model_id: str = Field(max_length=512)
    runtime_id: str = Field(max_length=256)
    scenario_id: str | None = Field(default=None, max_length=256)
    contract_id: str = Field(max_length=256)
    audit_mode: AuditMode
    execution_status: str = Field(max_length=64)
    data_source: Literal["warden_observation", "warden_plan_only", "none"] = "none"
    evidence_refs: list[str] = Field(default_factory=list, max_length=128)
    submitted_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0.0)
    run_config_artifact_id: str | None = Field(default=None, max_length=256)
    record_artifact_id: str | None = Field(default=None, max_length=256)
    blockers: list[str] = Field(default_factory=list, max_length=64)
    limitations: list[str] = Field(default_factory=list, max_length=64)
