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
    schema_version: Literal["0.1"] = "0.1"
    design_id: str = Field(default_factory=lambda: f"design-{uuid4().hex[:12]}")
    experiment_id: str
    hypothesis_id: str
    design_type: Literal["paired_replay", "negative_control", "factorial"] = "paired_replay"
    treatment_operation: str
    control_operation: str = "no_intervention"
    negative_control_operation: str | None = "sham_intervention"
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
        return self
