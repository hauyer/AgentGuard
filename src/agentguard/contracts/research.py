"""Contracts for immutable preregistration and auditable study analysis."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic.types import JsonValue

from agentguard.contracts.base import ContractModel


class StudyRegistrationRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    title: str = Field(min_length=8, max_length=240)
    research_question: str = Field(min_length=12, max_length=2000)
    primary_hypothesis: str = Field(min_length=12, max_length=2000)
    primary_outcome: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$")
    primary_direction: Literal["increase", "decrease", "two_sided"] = "two_sided"
    secondary_outcomes: list[str] = Field(default_factory=list, max_length=30)
    analysis_method: Literal["paired", "clustered_paired"] = "clustered_paired"
    multiplicity_method: Literal["none", "holm", "benjamini_hochberg"] = "holm"
    alpha: float = Field(default=0.05, gt=0.0, lt=0.5)
    planned_pairs: int = Field(ge=2, le=100000)
    planned_case_families: int = Field(ge=2, le=10000)
    seed: int = 7
    matching_variables: list[str] = Field(
        default_factory=lambda: ["case_family", "seed", "model_config", "task_input"],
        min_length=1,
    )
    inclusion_criteria: list[str] = Field(min_length=1, max_length=50)
    exclusion_criteria: list[str] = Field(default_factory=list, max_length=50)
    stopping_rule: str = Field(min_length=8, max_length=1000)
    missing_data_rule: str = Field(min_length=8, max_length=1000)
    deviations_policy: str = Field(
        default="Any deviation is logged with timestamp, rationale, and whether it was decided before outcome inspection.",
        min_length=8,
        max_length=1000,
    )
    protocol_version: str = Field(default="1", min_length=1, max_length=80)
    code_revision: str = Field(default="unspecified", min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_outcomes(self):
        all_outcomes = [self.primary_outcome, *self.secondary_outcomes]
        if len(all_outcomes) != len(set(all_outcomes)):
            raise ValueError("primary_outcome and secondary_outcomes must be unique")
        if self.planned_case_families > self.planned_pairs:
            raise ValueError("planned_case_families cannot exceed planned_pairs")
        if self.analysis_method == "clustered_paired" and self.planned_case_families < 2:
            raise ValueError("clustered_paired analysis requires at least two planned case families")
        return self


class RegisteredStudy(StudyRegistrationRequest):
    study_id: str = Field(default_factory=lambda: f"study-{uuid4().hex[:12]}")
    status: Literal["registered"] = "registered"
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    registration_sha256: str = Field(min_length=64, max_length=64)


class OutcomePairs(ContractModel):
    control: list[float] = Field(min_length=2, max_length=100000)
    treatment: list[float] = Field(min_length=2, max_length=100000)
    case_families: list[str] = Field(min_length=2, max_length=100000)

    @model_validator(mode="after")
    def validate_alignment(self):
        if not (len(self.control) == len(self.treatment) == len(self.case_families)):
            raise ValueError("control, treatment, and case_families must have the same length")
        if len(set(self.case_families)) < 2:
            raise ValueError("at least two distinct case families are required")
        return self


class StudyAnalysisRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    outcomes: dict[str, OutcomePairs] = Field(min_length=1, max_length=31)
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    source_artifact_refs: list[str] = Field(default_factory=list, max_length=1000)
    data_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bootstrap_draws: int = Field(default=5000, ge=100, le=100000)
    permutation_draws: int = Field(default=20000, ge=1000, le=1000000)


class StudyAnalysisReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    analysis_id: str = Field(default_factory=lambda: f"analysis-{uuid4().hex[:12]}")
    study_id: str
    registration_sha256: str
    analysis_sha256: str
    evidence_mode: Literal["synthetic", "observed", "measured"]
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    primary_outcome: str
    outcomes: dict[str, dict[str, JsonValue]]
    multiplicity_method: Literal["none", "holm", "benjamini_hochberg"]
    alpha: float
    confirmatory_primary_result: bool
    source_artifact_refs: list[str] = Field(default_factory=list)
    data_sha256: str | None = None
    report_artifact_id: str | None = None
    deviations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
