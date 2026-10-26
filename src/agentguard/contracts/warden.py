"""Versioned boundary contracts for delegating low-level audits to WARDEN."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agentguard.contracts.base import ContractModel
from agentguard.contracts.truth_guard import find_hidden_truth_fields


class WardenCaptureTarget(ContractModel):
    target_id: str = Field(min_length=1, max_length=256)
    agent_ids: list[str] = Field(min_length=1, max_length=64)
    module: str | None = None
    layer: int | None = Field(default=None, ge=0)
    component: str | None = "resid_post"
    token_policy: Literal["all", "last_token", "mean_tokens"] = "last_token"
    retain_gradient: bool = False

    @field_validator("agent_ids")
    @classmethod
    def nonblank_agent_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("agent_ids must not contain blank values")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("agent_ids must be unique")
        return cleaned

    @model_validator(mode="after")
    def require_activation_coordinate(self) -> WardenCaptureTarget:
        if self.module is None and self.layer is None:
            raise ValueError("capture target must declare module or layer")
        return self


class WardenAuditRequest(ContractModel):
    """Request accepted by WARDEN `/v1/audit-plans` (not an AgentGuard local audit)."""
    request_id: str = Field(min_length=1, max_length=256)
    contract_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=512)
    runtime_id: str = Field(min_length=1, max_length=256)
    mode: Literal[
        "observation", "capture_only", "detection", "causal_audit", "defense_evaluation"
    ] = "causal_audit"
    probe_id: str | None = None
    sae_id: str | None = None
    capture_targets: list[WardenCaptureTarget] = Field(default_factory=list, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # AgentGuard control-plane fields; never sent to WARDEN's strict domain contract.
    authorization_ref: str | None = Field(default=None, max_length=256)
    scenario_id: str | None = Field(default=None, min_length=1, max_length=256)
    risk_type: str | None = Field(default=None, min_length=1, max_length=256)
    start_run: bool = True
    wait_for_completion: bool = False
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=10.0)

    @field_validator("metadata")
    @classmethod
    def reject_hidden_truth_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        present = find_hidden_truth_fields(value)
        if present:
            raise ValueError(f"metadata must not contain hidden evaluator fields: {sorted(present)}")
        return value

    def to_warden_payload(self) -> dict[str, Any]:
        """Project the AgentGuard request onto WARDEN's public REST contract."""
        payload = self.model_dump(mode="json", exclude={
            "start_run", "authorization_ref", "scenario_id", "risk_type",
            "wait_for_completion", "timeout_seconds", "poll_interval_seconds",
        })
        # `observation` is an AgentGuard workflow label. WARDEN's AuditMode enum
        # represents the corresponding non-causal collection as `capture_only`.
        if payload["mode"] == "observation":
            payload["mode"] = "capture_only"
        return payload


class WardenAuditSubmission(ContractModel):
    status: Literal["planned", "queued", "running", "completed", "blocked", "failed", "unknown"]
    plan: dict[str, Any]
    run: dict[str, Any] | None = None
    message: str | None = None


class WardenDefenseExecuteRequest(ContractModel):
    """AgentGuard approval reference for a requested WARDEN defense action."""

    candidate_id: str = Field(min_length=1, max_length=256)
    authorization_ref: str | None = Field(default=None, max_length=256)
