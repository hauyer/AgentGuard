from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class MetricValue(ContractModel):
    name: str
    value: float
    unit: str = ""
    source: str = "unknown"
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    description: str = ""


class EvaluationReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    experiment_id: str
    status: Literal["completed", "partial", "failed"]
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    baseline_metrics: dict[str, MetricValue] = Field(default_factory=dict)
    post_intervention_metrics: dict[str, MetricValue] = Field(default_factory=dict)
    deltas: dict[str, float] = Field(default_factory=dict)
    benign_utility_preserved: bool | None = None
    notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sample_size: int | None = Field(default=None, ge=0)
    repetitions: int | None = Field(default=None, ge=0)
    confidence_intervals: dict[str, dict[str, float]] = Field(default_factory=dict)
    statistical_tests: dict[str, str | float | int | bool] = Field(default_factory=dict)
