from typing import Literal
from pydantic import Field
from agentguard.contracts.base import ContractModel


class InterventionRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    experiment_id: str
    intervention_id: str
    target_type: Literal["message", "tool_call", "tool_result", "activation"]
    target_ref: str = Field(min_length=1, max_length=256)
    operation: Literal["block", "replace", "ablate", "patch"]
    control_group: str | None = None
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    authorization_ref: str | None = None


class InterventionResult(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    intervention_id: str
    status: Literal["completed", "rejected", "unsupported", "runtime_error"]
    changed: bool
    effect_status: Literal["not_measured", "simulated", "measured"] = "not_measured"
    evidence_refs: list[str] = Field(default_factory=list)
    message: str = ""
    error_code: str | None = None
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    before_artifact_ref: str | None = None
    after_artifact_ref: str | None = None
    execution_duration_seconds: float | None = Field(default=None, ge=0.0)
    backend_version: str = "unknown"
