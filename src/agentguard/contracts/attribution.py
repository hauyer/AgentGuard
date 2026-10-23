"""Contracts for secret-information-flow mediation and path-shift analysis."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
import math
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic.types import JsonValue

from agentguard.contracts.base import ContractModel


class AttributionNodeAnalysisRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    node_id: str = Field(min_length=1, max_length=256)
    node_type: Literal[
        "sender_activation", "sender_logits", "sender_cot", "public_token",
        "receiver_activation", "receiver_cot", "receiver_output", "tool_result", "other",
    ]
    baseline_secret_recovery: list[float] = Field(min_length=2, max_length=100000)
    intervened_secret_recovery: list[float] = Field(min_length=2, max_length=100000)
    baseline_collusion_success: list[float] | None = Field(default=None, min_length=2, max_length=100000)
    intervened_collusion_success: list[float] | None = Field(default=None, min_length=2, max_length=100000)
    baseline_benign_utility: list[float] | None = Field(default=None, min_length=2, max_length=100000)
    intervened_benign_utility: list[float] | None = Field(default=None, min_length=2, max_length=100000)
    case_families: list[str] = Field(min_length=2, max_length=100000)
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    source_artifact_refs: list[str] = Field(default_factory=list, max_length=1000)
    seed: int = 7
    bootstrap_draws: int = Field(default=5000, ge=100, le=100000)
    permutation_draws: int = Field(default=20000, ge=1000, le=1000000)

    @model_validator(mode="after")
    def validate_measurements(self):
        n = len(self.baseline_secret_recovery)
        arrays = {
            "intervened_secret_recovery": self.intervened_secret_recovery,
            "baseline_collusion_success": self.baseline_collusion_success,
            "intervened_collusion_success": self.intervened_collusion_success,
            "baseline_benign_utility": self.baseline_benign_utility,
            "intervened_benign_utility": self.intervened_benign_utility,
        }
        if len(self.case_families) != n:
            raise ValueError("case_families length must match recovery arrays")
        if len(set(self.case_families)) < 2:
            raise ValueError("at least two case families are required")
        for name, values in arrays.items():
            if values is None:
                continue
            if len(values) != n:
                raise ValueError(f"{name} must have the same length as baseline_secret_recovery")
            if any(not 0.0 <= float(value) <= 1.0 for value in values):
                raise ValueError(f"{name} values must be in [0, 1]")
        for name, values in {
            "baseline_secret_recovery": self.baseline_secret_recovery,
            "intervened_secret_recovery": self.intervened_secret_recovery,
        }.items():
            if any(not 0.0 <= float(value) <= 1.0 for value in values):
                raise ValueError(f"{name} values must be in [0, 1]")
        for baseline, intervention, label in [
            (self.baseline_collusion_success, self.intervened_collusion_success, "collusion_success"),
            (self.baseline_benign_utility, self.intervened_benign_utility, "benign_utility"),
        ]:
            if (baseline is None) != (intervention is None):
                raise ValueError(f"baseline and intervened {label} arrays must be supplied together")
        return self


class AttributionNodeAnalysisReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    analysis_id: str = Field(default_factory=lambda: f"attr-{uuid4().hex[:12]}")
    node_id: str
    node_type: str
    evidence_mode: Literal["synthetic", "observed", "measured"]
    secret_recovery_mediation: dict[str, JsonValue]
    collusion_success_mediation: dict[str, JsonValue] | None = None
    benign_utility_change: dict[str, JsonValue] | None = None
    source_artifact_refs: list[str] = Field(default_factory=list)
    report_artifact_id: str | None = None
    causal_claim_status: Literal["candidate_only", "causal_validation_required"] = "causal_validation_required"
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PathShiftAnalysisRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    protocol_fingerprint_before: str = Field(min_length=8, max_length=256)
    protocol_fingerprint_after: str = Field(min_length=8, max_length=256)
    model_state_before: str = Field(min_length=1, max_length=128)
    model_state_after: str = Field(min_length=1, max_length=128)
    contributions_before: dict[str, float] = Field(min_length=1, max_length=10000)
    contributions_after: dict[str, float] = Field(min_length=1, max_length=10000)
    top_k: int = Field(default=5, ge=1, le=1000)
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    source_artifact_refs: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_contributions(self):
        if self.protocol_fingerprint_before != self.protocol_fingerprint_after:
            raise ValueError("path-shift analysis requires the same attribution protocol fingerprint before and after")
        for label, values in [("before", self.contributions_before), ("after", self.contributions_after)]:
            if any(not key.strip() for key in values):
                raise ValueError(f"path contribution node IDs in {label} must be non-empty")
            if any(float(value) < 0 or not math.isfinite(float(value)) for value in values.values()):
                raise ValueError(f"path contributions in {label} must be finite and non-negative")
        if sum(self.contributions_before.values()) <= 0 or sum(self.contributions_after.values()) <= 0:
            raise ValueError("each path contribution vector must have positive total mass")
        return self


class PathShiftAnalysisReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    analysis_id: str = Field(default_factory=lambda: f"pathshift-{uuid4().hex[:12]}")
    protocol_fingerprint: str
    model_state_before: str
    model_state_after: str
    evidence_mode: Literal["synthetic", "observed", "measured"]
    metrics: dict[str, JsonValue]
    source_artifact_refs: list[str] = Field(default_factory=list)
    report_artifact_id: str | None = None
    interpretation: str
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
