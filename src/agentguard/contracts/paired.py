from typing import Literal

from pydantic import Field
from pydantic.types import JsonValue

from agentguard.contracts.base import ContractModel
from agentguard.contracts.intervention import InterventionRequest, InterventionResult


class PairedExperimentRunRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    design_id: str | None = None
    action_id: str | None = None
    authorization_ref: str | None = None


class PairedExperimentPairResult(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    seed: int
    status: Literal["completed", "partial", "failed"]
    control_experiment_id: str
    treatment_experiment_id: str
    negative_control_experiment_id: str
    control_observation_count: int = Field(default=0, ge=0)
    treatment_observation_count: int = Field(default=0, ge=0)
    negative_control_observation_count: int = Field(default=0, ge=0)
    control_metrics: dict[str, float] = Field(default_factory=dict)
    treatment_metrics: dict[str, float] = Field(default_factory=dict)
    negative_control_metrics: dict[str, float] = Field(default_factory=dict)
    intervention_request: InterventionRequest | None = None
    intervention_result: InterventionResult | None = None
    error_code: str | None = None
    notes: list[str] = Field(default_factory=list)


class PairedExperimentExecutionReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    run_id: str
    experiment_id: str
    design_id: str
    hypothesis_id: str
    status: Literal["completed", "partial", "failed"]
    evidence_mode: Literal["synthetic", "observed", "measured"]
    causal_claim_status: Literal["synthetic_only", "not_assessed", "causal_validation_required"]
    requested_pairs: int = Field(ge=2)
    completed_pairs: int = Field(ge=0)
    pairs: list[PairedExperimentPairResult] = Field(default_factory=list)
    aggregate_statistics: dict[str, JsonValue] = Field(default_factory=dict)
    negative_control_statistics: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_id: str | None = None
    limitations: list[str] = Field(default_factory=list)
