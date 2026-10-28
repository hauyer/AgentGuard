"""Contracts for immutable preregistration and auditable study analysis."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic.types import JsonValue

from agentguard.contracts.base import ContractModel
from agentguard.contracts.truth_guard import find_hidden_truth_fields

ResearchTrack = Literal["mechanism_testbed", "live_steganography"]
ResearchMetric = Literal[
    "SRR_ITT", "SRR_executed", "BER", "NORMAL_UTILITY", "CARRIER_DELIVERY_RATE",
    "FALSE_POSITIVE_COST", "LATENCY", "GPU_COST", "PROBE_SCORE", "CUSTOM",
]
LabelSemantics = Literal["decoded_directive", "secret_bit", "misaligned"]


def _is_local_path_reference(value: str) -> bool:
    return (
        value.casefold().startswith(("file:", "/", "~", "./", "../", ".\\", "..\\"))
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value) is not None
    )


class ResearchArmDefinition(ContractModel):
    """An immutable planned condition; this does not submit or execute a run."""

    arm_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    role: Literal["baseline", "treatment", "sham", "irrelevant", "randomization"]
    method_id: str = Field(min_length=1, max_length=128)
    method_version: str = Field(min_length=1, max_length=128)
    target_ref: str | None = Field(default=None, max_length=256)
    intervention_level: str | None = Field(default=None, max_length=128)
    control_arm_id: str | None = Field(default=None, max_length=128)
    parameter_ref: str | None = Field(default=None, max_length=256)
    parameter_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_references(self):
        if (self.parameter_ref is None) != (self.parameter_sha256 is None):
            raise ValueError("parameter_ref and parameter_sha256 must be provided together")
        for name, value in (("target_ref", self.target_ref), ("parameter_ref", self.parameter_ref)):
            if value is not None and _is_local_path_reference(value):
                raise ValueError(f"{name} must be a public or content-addressed reference, not a local path")
        return self


class ProbeEffectAssociationInput(ContractModel):
    """Held-out family-level Probe scores paired with measured intervention effects."""

    probe_scores: list[float] = Field(min_length=4, max_length=100000)
    intervention_effects: list[float] = Field(min_length=4, max_length=100000)
    case_families: list[str] = Field(min_length=4, max_length=100000)
    probe_scope: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=256)
    layer: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=128)
    model_revision: str = Field(min_length=1, max_length=128)
    label_semantics: LabelSemantics
    split: Literal["test"] = "test"

    @model_validator(mode="after")
    def validate_alignment(self):
        if not (len(self.probe_scores) == len(self.intervention_effects) == len(self.case_families)):
            raise ValueError("Probe scores, intervention effects, and case families must align")
        if len(set(self.case_families)) < 2 or any(not value.strip() for value in self.case_families):
            raise ValueError("at least two non-empty case families are required")
        if _is_local_path_reference(self.target):
            raise ValueError("Probe target must be a coordinate reference, not a local path")
        return self


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
    track: ResearchTrack | None = None
    protocol_ref: str | None = Field(default=None, min_length=1, max_length=512)
    dataset_ref: str | None = Field(default=None, min_length=1, max_length=512)
    split_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    case_family_ref: str | None = Field(default=None, min_length=1, max_length=512)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_revision: str | None = Field(default=None, min_length=1, max_length=128)
    tokenizer_revision: str | None = Field(default=None, min_length=1, max_length=128)
    decoding_config_ref: str | None = Field(default=None, min_length=1, max_length=512)
    probe_scope: str | None = Field(default=None, max_length=128)
    probe_target: str | None = Field(default=None, max_length=256)
    probe_layer: str | None = Field(default=None, max_length=128)
    probe_token: str | None = Field(default=None, max_length=128)
    probe_validation_ref: str | None = Field(default=None, max_length=512)
    label_semantics: LabelSemantics | None = None
    metric_definitions: dict[str, ResearchMetric] = Field(default_factory=dict, max_length=31)
    arms: list[ResearchArmDefinition] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_outcomes(self):
        all_outcomes = [self.primary_outcome, *self.secondary_outcomes]
        if len(all_outcomes) != len(set(all_outcomes)):
            raise ValueError("primary_outcome and secondary_outcomes must be unique")
        if self.planned_case_families > self.planned_pairs:
            raise ValueError("planned_case_families cannot exceed planned_pairs")
        if self.analysis_method == "clustered_paired" and self.planned_case_families < 2:
            raise ValueError("clustered_paired analysis requires at least two planned case families")
        for name, value in (
            ("protocol_ref", self.protocol_ref),
            ("dataset_ref", self.dataset_ref),
            ("case_family_ref", self.case_family_ref),
            ("decoding_config_ref", self.decoding_config_ref),
            ("probe_validation_ref", self.probe_validation_ref),
        ):
            if value is not None and _is_local_path_reference(value):
                raise ValueError(f"{name} must be a public or content-addressed reference, not a local path")
        if self.metric_definitions and set(self.metric_definitions) != set(all_outcomes):
            raise ValueError("metric_definitions must name every preregistered outcome exactly once")
        if self.track is not None:
            required = {
                "protocol_ref": self.protocol_ref,
                "dataset_ref": self.dataset_ref,
                "split_sha256": self.split_sha256,
                "case_family_ref": self.case_family_ref,
                "model_id": self.model_id,
                "model_revision": self.model_revision,
                "tokenizer_revision": self.tokenizer_revision,
                "decoding_config_ref": self.decoding_config_ref,
                "label_semantics": self.label_semantics,
                "metric_definitions": self.metric_definitions,
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError(f"tracked study is missing frozen metadata: {', '.join(missing)}")
            if not self.arms:
                raise ValueError("tracked study requires explicit baseline and treatment arms")
        probe_fields = (self.probe_scope, self.probe_target, self.probe_layer, self.probe_token)
        if any(probe_fields) and not all(probe_fields):
            raise ValueError("Probe scope, target, layer, and token must be frozen together")
        if "PROBE_SCORE" in self.metric_definitions.values() and not (
            all(probe_fields) and self.probe_validation_ref
        ):
            raise ValueError("PROBE_SCORE requires frozen Probe coordinates and a validation reference")
        if self.arms:
            arm_ids = [arm.arm_id for arm in self.arms]
            if len(arm_ids) != len(set(arm_ids)):
                raise ValueError("arm_id values must be unique within a study")
            baseline_ids = [arm.arm_id for arm in self.arms if arm.role == "baseline"]
            if len(baseline_ids) != 1 or not any(arm.role == "treatment" for arm in self.arms):
                raise ValueError("arm plan requires exactly one baseline and at least one treatment")
            for arm in self.arms:
                if arm.role == "baseline" and arm.control_arm_id is not None:
                    raise ValueError("baseline arm cannot reference another control arm")
                if arm.role != "baseline" and arm.control_arm_id != baseline_ids[0]:
                    raise ValueError("each non-baseline arm must reference the frozen baseline arm")
        hidden = find_hidden_truth_fields(self.model_dump(mode="python"))
        if hidden:
            raise ValueError(f"hidden evaluator-only fields are not accepted: {sorted(hidden)}")
        return self


class RegisteredStudy(StudyRegistrationRequest):
    study_id: str = Field(default_factory=lambda: f"study-{uuid4().hex[:12]}")
    status: Literal["registered"] = "registered"
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
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


class StudyArmAnalysisInput(ContractModel):
    """Outcomes for one planned arm, preserving failed and unavailable arms."""

    status: Literal["completed", "partial", "failed", "blocked", "not_run", "unknown"]
    outcomes: dict[str, OutcomePairs] = Field(default_factory=dict, max_length=31)
    failure_reason: str | None = Field(default=None, max_length=512)
    unknown_reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status == "completed" and not self.outcomes:
            raise ValueError("completed arm analysis requires outcomes")
        if self.status in {"failed", "blocked", "not_run", "unknown"} and self.outcomes:
            raise ValueError("non-completed arm status cannot carry outcome values")
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("failed arm requires failure_reason")
        if self.status in {"blocked", "not_run", "unknown"} and not self.unknown_reason:
            raise ValueError("blocked, not_run, and unknown arms require unknown_reason")
        return self


class StudyAnalysisRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    outcomes: dict[str, OutcomePairs] = Field(min_length=1, max_length=31)
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    source_artifact_refs: list[str] = Field(default_factory=list, max_length=1000)
    data_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bootstrap_draws: int = Field(default=5000, ge=100, le=100000)
    permutation_draws: int = Field(default=20000, ge=1000, le=1000000)
    arm_analyses: dict[str, StudyArmAnalysisInput] = Field(default_factory=dict, max_length=64)
    probe_effect_association: ProbeEffectAssociationInput | None = None

    @model_validator(mode="after")
    def reject_hidden_truth(self):
        hidden = find_hidden_truth_fields(self.model_dump(mode="python"))
        if hidden:
            raise ValueError(f"hidden evaluator-only fields are not accepted: {sorted(hidden)}")
        if any(_is_local_path_reference(ref) for ref in self.source_artifact_refs):
            raise ValueError("source_artifact_refs must not contain local file paths")
        return self


class StudyAnalysisReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    analysis_id: str = Field(default_factory=lambda: f"analysis-{uuid4().hex[:12]}")
    study_id: str
    registration_sha256: str
    analysis_sha256: str
    evidence_mode: Literal["synthetic", "observed", "measured"]
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
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
    arm_analyses: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    rq2_probe_effect_association: dict[str, JsonValue] | None = None
    rq3_safety_utility: list[dict[str, JsonValue]] = Field(default_factory=list)
    causal_claim_status: Literal["synthetic_only", "not_assessed"] = "not_assessed"
    source_validation: Literal["unverified", "synthetic_only"] = "unverified"
