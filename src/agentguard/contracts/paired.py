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
    schema_version: Literal["0.1", "0.2"] = "0.2"
    seed: int
    status: Literal["completed", "partial", "failed"]
    control_experiment_id: str
    treatment_experiment_id: str
    negative_control_experiment_id: str
    sham_control_experiment_id: str | None = None
    unrelated_position_control_experiment_id: str | None = None
    sham_control_status: Literal["not_run", "completed", "partial", "failed", "unsupported"] = "not_run"
    unrelated_position_control_status: Literal["not_run", "completed", "partial", "failed", "unsupported"] = "not_run"
    randomization_baseline_status: Literal["not_run", "completed", "partial", "failed", "unsupported"] = "not_run"
    control_observation_count: int = Field(default=0, ge=0)
    treatment_observation_count: int = Field(default=0, ge=0)
    negative_control_observation_count: int = Field(default=0, ge=0)
    sham_control_observation_count: int = Field(default=0, ge=0)
    unrelated_position_control_observation_count: int = Field(default=0, ge=0)
    randomization_baseline_observation_count: int = Field(default=0, ge=0)
    control_metrics: dict[str, float] = Field(default_factory=dict)
    treatment_metrics: dict[str, float] = Field(default_factory=dict)
    negative_control_metrics: dict[str, float] = Field(default_factory=dict)
    sham_control_metrics: dict[str, float] = Field(default_factory=dict)
    unrelated_position_control_metrics: dict[str, float] = Field(default_factory=dict)
    randomization_baseline_metrics: dict[str, float] = Field(default_factory=dict)
    control_output_artifact_ref: str | None = None
    treatment_output_artifact_ref: str | None = None
    sham_control_output_artifact_ref: str | None = None
    unrelated_position_control_output_artifact_ref: str | None = None
    randomization_baseline_experiment_id: str | None = None
    randomization_baseline_output_artifact_ref: str | None = None
    intervention_request: InterventionRequest | None = None
    intervention_result: InterventionResult | None = None
    error_code: str | None = None
    notes: list[str] = Field(default_factory=list)


class PairedExperimentExecutionReport(ContractModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    run_id: str
    experiment_id: str
    design_id: str
    hypothesis_id: str
    status: Literal["completed", "partial", "failed"]
    evidence_mode: Literal["synthetic", "observed", "measured"]
    causal_claim_status: Literal["synthetic_only", "not_assessed", "causal_validation_required"]
    design_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preregistration_artifact_ref: str | None = None
    preregistration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_revision: str | None = None
    decoding_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    control_condition: str | None = None
    treatment_condition: str | None = None
    control_operation: str | None = None
    treatment_operation: str | None = None
    sham_control_operation: str | None = None
    unrelated_position_control_operation: str | None = None
    randomization_or_permutation_baseline: str | None = None
    hidden_truth_excluded_from_model_input: bool | None = None
    execution_log_artifact_ref: str | None = None
    execution_log_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    intervention_parameters_artifact_ref: str | None = None
    intervention_parameters_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evaluator_version: str | None = None
    statistical_report_artifact_ref: str | None = None
    independent_case_family_ids: list[str] = Field(default_factory=list)
    requested_pairs: int = Field(ge=2)
    completed_pairs: int = Field(ge=0)
    pairs: list[PairedExperimentPairResult] = Field(default_factory=list)
    aggregate_statistics: dict[str, JsonValue] = Field(default_factory=dict)
    negative_control_statistics: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_id: str | None = None
    limitations: list[str] = Field(default_factory=list)
