import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from agentguard.contracts.base import ContractModel


class InvestigationHypothesis(ContractModel):
    """Explicit, falsifiable hypothesis tied to observed evidence."""
    schema_version: Literal["0.1"] = "0.1"
    hypothesis_id: str = Field(default_factory=lambda: f"hyp-{uuid4().hex[:12]}")
    experiment_id: str
    finding_id: str
    target_ref: str
    statement: str = Field(min_length=8, max_length=1024)
    expected_effect: str = Field(min_length=4, max_length=1024)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: Literal["proposed", "under_test", "supported", "weakened", "rejected", "inconclusive"] = "proposed"
    next_test: str = "matched_replay"


class InvestigationAction(ContractModel):
    """Rankable experiment action; scores are heuristics, not probabilities."""
    schema_version: Literal["0.1"] = "0.1"
    action_id: str = Field(default_factory=lambda: f"act-{uuid4().hex[:12]}")
    hypothesis_id: str
    target_type: Literal["message", "tool_call", "tool_result", "activation"]
    target_ref: str
    operation: Literal["block", "replace", "ablate", "patch"]
    expected_information_gain: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(gt=0.0)
    estimated_risk: float = Field(ge=0.0, le=1.0)
    utility_score: float = Field(ge=0.0)
    rationale: str


class PairedExperimentDesign(ContractModel):
    """Reproducible paired treatment/control design metadata."""
    schema_version: Literal["0.1", "0.2"] = "0.2"
    design_id: str = Field(default_factory=lambda: f"design-{uuid4().hex[:12]}")
    experiment_id: str
    hypothesis_id: str
    design_type: Literal["paired_replay", "negative_control", "factorial"] = "paired_replay"
    treatment_operation: str
    control_operation: str = "no_intervention"
    sham_control_operation: str | None = None
    negative_control_operation: str | None = None
    scenario_id: str | None = None
    model_revision: str | None = None
    decoding_settings: dict[str, str | int | float | bool] = Field(default_factory=dict)
    control_condition: str | None = None
    treatment_condition: str | None = None
    candidate_target_type: Literal["message", "tool_call", "tool_result", "activation"] | None = None
    candidate_target_ref: str | None = None
    candidate_component: str | None = None
    token_position: int | None = None
    utility_metric: str | None = None
    secret_recovery_metric: str | None = None
    stopping_condition: str | None = None
    exclusion_rules: list[str] = Field(default_factory=list)
    randomization_or_permutation_baseline: str | None = None
    hidden_truth_excluded_from_model_input: bool = False
    independent_case_family_ids: list[str] = Field(default_factory=list)
    preregistration_artifact_ref: str | None = None
    preregistration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    seed: int
    seeds: list[int] = Field(min_length=2)
    repetitions: int = Field(ge=2, le=10000)
    matched_on: list[str] = Field(default_factory=lambda: ["scenario", "seed", "model_config", "task_input"])
    primary_metric: str = "risk_exposure"
    secondary_metrics: list[str] = Field(default_factory=lambda: ["benign_utility", "latency_seconds"])
    preregistered: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_seed_schedule(self):
        if self.repetitions != len(self.seeds):
            raise ValueError("repetitions must equal the number of declared seeds")
        if self.seeds and self.seed != self.seeds[0]:
            raise ValueError("seed must match the first entry in the seed schedule")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seed schedule must not contain duplicate seeds")
        if self.preregistered:
            required = {
                "scenario_id": self.scenario_id,
                "model_revision": self.model_revision,
                "control_condition": self.control_condition,
                "treatment_condition": self.treatment_condition,
                "candidate_target_type": self.candidate_target_type,
                "candidate_target_ref": self.candidate_target_ref,
                "sham_control_operation": self.sham_control_operation,
                "negative_control_operation": self.negative_control_operation,
                "utility_metric": self.utility_metric,
                "secret_recovery_metric": self.secret_recovery_metric,
                "stopping_condition": self.stopping_condition,
                "randomization_or_permutation_baseline": self.randomization_or_permutation_baseline,
                "preregistration_artifact_ref": self.preregistration_artifact_ref,
                "preregistration_sha256": self.preregistration_sha256,
            }
            missing = sorted(name for name, value in required.items() if value is None or value == "")
            if not self.decoding_settings:
                missing.append("decoding_settings")
            if not self.exclusion_rules:
                missing.append("exclusion_rules")
            if not self.hidden_truth_excluded_from_model_input:
                missing.append("hidden_truth_excluded_from_model_input")
            if len(set(self.independent_case_family_ids)) != len(self.independent_case_family_ids):
                raise ValueError("independent case-family IDs must be distinct")
            if len(self.independent_case_family_ids) < 2:
                missing.append("independent_case_family_ids")
            if set(self.independent_case_family_ids) & {"", "unknown", "null"}:
                raise ValueError("independent case-family IDs must be explicit")
            required_matching = {
                "scenario_id", "task_input", "model_revision", "decoding_settings",
                "case_family", "seed",
            }
            if not required_matching.issubset(set(self.matched_on)):
                missing.append("matched_on[scenario_id,task_input,model_revision,decoding_settings,case_family,seed]")
            if self.control_condition == self.treatment_condition:
                raise ValueError("control and treatment conditions must differ")
            if self.sham_control_operation in {self.treatment_operation, self.negative_control_operation}:
                raise ValueError("sham, treatment and unrelated-position control operations must be distinct")
            if self.negative_control_operation == self.treatment_operation:
                raise ValueError("unrelated-position control operation must differ from treatment")
            if missing:
                raise ValueError("pre-registered paired design is incomplete: " + ", ".join(missing))
        return self

    @property
    def decoding_settings_sha256(self) -> str:
        canonical = json.dumps(self.decoding_settings, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def design_fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
