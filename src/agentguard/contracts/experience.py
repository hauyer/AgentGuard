"""Versioned experience records and immutable recommendation policy contracts."""

import re
from datetime import UTC, datetime
from math import isfinite
from typing import Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agentguard.contracts.base import ContractModel

EvidenceLevel = Literal["observational", "synthetic", "interventional", "causal_validated"]
DatasetPartition = Literal["training", "validation", "test", "held_out_family"]


class ExperienceRecord(ContractModel):
    """A provenance-bearing case memory; defaults are quarantined from reward use."""

    schema_version: Literal["0.1", "0.2"] = "0.2"
    experience_id: str = Field(default_factory=lambda: f"exp-mem-{uuid4().hex[:12]}")
    scenario_id: str
    case_family: str = "unspecified"
    dataset_partition: DatasetPartition = "held_out_family"
    risk_type: str
    source_system: Literal["warden", "agentguard", "external", "legacy"] = "legacy"
    source_run_status: Literal["completed", "blocked", "failed", "cancelled", "unknown"] = "unknown"
    evidence_provenance_verified: bool = False
    audit_run_id: str | None = None
    hypothesis: str | None = None
    hypothesis_summary: str
    selected_action: str | None = None
    action_type: str
    action_summary: str
    outcome: Literal["success", "partial", "failure", "inconclusive"]
    observed_outcome: dict[str, float] = Field(default_factory=dict)
    evidence_grade: EvidenceLevel
    verification_level: EvidenceLevel | None = None
    verification_status: Literal["unverified", "reviewed", "verified"] = "unverified"
    repetitions: int = Field(default=1, ge=1)
    evidence_refs: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    benign_utility: dict[str, float] = Field(default_factory=dict)
    execution_cost: float | None = Field(default=None, ge=0)
    metrics: dict[str, float] = Field(default_factory=dict)
    model_version: str = "unknown"
    code_version: str = "unknown"
    policy_version: str = "unknown"
    reproducibility_hash: str | None = None
    limitations: list[str] = Field(default_factory=list)
    seed: int | None = None
    allow_auto_recommendation: bool = False
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list)

    @field_validator("verified_at", "expires_at", "created_at")
    @classmethod
    def require_timezone_aware_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("experience timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_versioned_experience(self):
        if self.hypothesis is None:
            object.__setattr__(self, "hypothesis", self.hypothesis_summary)
        if self.selected_action is None:
            object.__setattr__(self, "selected_action", f"{self.action_type}: {self.action_summary}")
        if self.verification_level is None:
            object.__setattr__(self, "verification_level", self.evidence_grade)
        elif self.verification_level != self.evidence_grade:
            raise ValueError("verification_level must match the recorded evidence_grade")
        if not self.observed_outcome and self.metrics:
            object.__setattr__(self, "observed_outcome", dict(self.metrics))

        for ref in self.evidence_refs:
            lowered = ref.strip().lower()
            parsed_ref = urlsplit(ref)
            credential_query_keys = {
                key.lower() for key, _value in parse_qsl(parsed_ref.query, keep_blank_values=True)
            } & {"token", "access_token", "api_key", "key", "secret", "auth", "signature"}
            if (
                not lowered
                or lowered.startswith(("file:", "\\\\", "/"))
                or re.match(r"^[a-z]:[\\/]", lowered)
                or (parsed_ref.scheme.lower() in {"http", "https"} and bool(parsed_ref.username or parsed_ref.password or credential_query_keys))
            ):
                raise ValueError("evidence_refs must be safe opaque references, not local paths")
        for mapping in (self.metrics, self.observed_outcome, self.benign_utility):
            if any(not isfinite(value) for value in mapping.values()):
                raise ValueError("experience measurements must be finite numbers")

        if self.expires_at and self.verified_at and self.expires_at <= self.verified_at:
            raise ValueError("expires_at must be later than verified_at")
        if self.dataset_partition == "training" and self.case_family == "unspecified":
            raise ValueError("training experiences require a named case_family")

        if self.verification_status == "verified":
            if self.repetitions < 2:
                raise ValueError("verified experiences require at least two repetitions")
            if not self.evidence_refs:
                raise ValueError("verified experiences require evidence_refs")
            if not self.validation_notes:
                raise ValueError("verified experiences require validation_notes")
            if self.evidence_grade not in {"interventional", "causal_validated"}:
                raise ValueError("verified experiences require interventional evidence or stronger")
            if self.schema_version == "0.2":
                if not self.audit_run_id or not self.audit_run_id.strip():
                    raise ValueError("verified experiences require audit_run_id")
                if self.source_system != "warden" or self.source_run_status != "completed":
                    raise ValueError("verified reward evidence must come from a completed WARDEN run")
                if not self.evidence_provenance_verified:
                    raise ValueError("verified experiences require verified evidence provenance")
                if not self.metrics or not self.observed_outcome:
                    raise ValueError("verified experiences require measured outcome metrics")
                if not self.benign_utility:
                    raise ValueError("verified experiences require benign_utility measurements")
                if self.execution_cost is None:
                    raise ValueError("verified experiences require an execution_cost measurement")
                if self.model_version == "unknown" or self.policy_version == "unknown":
                    raise ValueError("verified experiences require model and policy versions")
                if self.dataset_partition != "training":
                    raise ValueError("only training-partition experiences may be marked verified")
                if self.case_family == "unspecified":
                    raise ValueError("verified experiences require a named case_family")
                if self.verified_at is None:
                    raise ValueError("verified experiences require verified_at")
                if not self.reproducibility_hash or not re.fullmatch(r"[0-9a-fA-F]{64}", self.reproducibility_hash):
                    raise ValueError("verified experiences require a 64-character reproducibility_hash")
        if self.allow_auto_recommendation and self.verification_status != "verified":
            raise ValueError("only verified experiences may be auto-recommended")
        return self

    def reward_ineligibility_reasons(
        self,
        *,
        now: datetime | None = None,
        conflicted: bool = False,
    ) -> list[str]:
        """Return all gates that prevent this record from contributing reward."""
        now = now or datetime.now(UTC)
        reasons: list[str] = []
        if self.schema_version != "0.2":
            reasons.append("legacy_schema_requires_revalidation")
        if self.dataset_partition != "training":
            reasons.append("not_training_partition")
        if self.verification_status != "verified":
            reasons.append("not_verified")
        if not self.allow_auto_recommendation:
            reasons.append("recommendation_not_enabled")
        if self.evidence_grade not in {"interventional", "causal_validated"}:
            reasons.append("evidence_not_interventional")
        if self.outcome == "inconclusive":
            reasons.append("outcome_inconclusive")
        if not self.evidence_refs:
            reasons.append("missing_evidence_refs")
        if self.repetitions < 2:
            reasons.append("not_repeated")
        if not self.metrics or not self.observed_outcome:
            reasons.append("missing_evaluation_metrics")
        if not self.benign_utility:
            reasons.append("missing_benign_utility")
        if self.execution_cost is None:
            reasons.append("missing_execution_cost")
        if not self.audit_run_id:
            reasons.append("missing_audit_run_id")
        if self.source_system != "warden" or self.source_run_status != "completed":
            reasons.append("source_not_completed_warden_run")
        if not self.evidence_provenance_verified:
            reasons.append("evidence_provenance_unverified")
        if self.model_version == "unknown" or self.policy_version == "unknown":
            reasons.append("unknown_model_or_policy_version")
        if self.verified_at is None:
            reasons.append("missing_verified_at")
        if not self.reproducibility_hash:
            reasons.append("missing_reproducibility_hash")
        if self.expires_at is not None and self.expires_at <= now:
            reasons.append("expired")
        if conflicted:
            reasons.append("conflicting_evidence")
        return reasons


class ExperienceSearchResult(ContractModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    query: str = ""
    items: list[ExperienceRecord] = Field(default_factory=list)
    total: int = Field(ge=0)
    recommendation_note: str = (
        "Public retrieval excludes test and held-out families; unverified memory cannot contribute reward."
    )


class ExperienceRecommendationPolicy(ContractModel):
    """Immutable, versioned parameters for contextual Thompson decision support."""

    schema_version: Literal["0.1"] = "0.1"
    policy_version: str = Field(min_length=1, max_length=64)
    algorithm: Literal["beta_bernoulli_thompson_sampling"] = "beta_bernoulli_thompson_sampling"
    cost_penalty: float = Field(default=0.2, ge=0, le=1)
    cost_scale: float = Field(default=1.0, gt=0)
    minimum_repetitions: int = Field(default=2, ge=2)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
