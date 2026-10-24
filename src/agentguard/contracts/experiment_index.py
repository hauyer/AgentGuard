"""Versioned public contract for the read-only experiment index (v1.0).

The index aggregates already-persisted records (WARDEN workflow journal and
content-addressed run-report artifacts). It is not a second run database:
entries carry only what the source actually recorded. Metrics appear only
when the source measured them; unknown values stay ``null`` and are never
coerced to zero.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from agentguard.contracts.base import ContractModel

ExperimentExecutionStatus = Literal[
    "created", "planned", "queued", "running", "completed", "partial",
    "blocked", "failed", "cancelled", "timed_out", "unknown",
]

ExperimentDataSource = Literal["warden_workflow", "synthetic_report"]

ExperimentEvidenceLevel = Literal[
    "synthetic", "observational", "interventional", "causal_validated", "unknown",
]


class ExperimentMetrics(ContractModel):
    """Metrics actually present in the source record; absence stays null."""

    schema_version: Literal["1.0"] = "1.0"
    risk_indicators: dict[str, float] | None = None
    benign_utility: float | None = None
    risk_exposure: float | None = None
    model_tokens: int | None = None
    time_seconds: float | None = None
    source_metrics: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ExperimentSummary(ContractModel):
    """One indexed experiment record with provenance kept explicit."""

    schema_version: Literal["1.0"] = "1.0"
    experiment_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    request_id: str | None = Field(default=None, max_length=256)
    artifact_id: str | None = Field(default=None, max_length=256)
    scenario_id: str | None = Field(default=None, max_length=256)
    model_id: str | None = Field(default=None, max_length=512)
    runtime_id: str | None = Field(default=None, max_length=256)
    mode: str | None = Field(default=None, max_length=64)
    execution_status: ExperimentExecutionStatus
    raw_status: str = Field(max_length=64)
    data_source: ExperimentDataSource
    evidence_level: ExperimentEvidenceLevel
    created_at: str | None = None
    updated_at: str | None = None
    metrics_available: bool = False


class ExperimentListResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[ExperimentSummary] = Field(default_factory=list, max_length=500)
    count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    next_offset: int | None = None
    total: int = Field(ge=0)
    limitations: list[str] = Field(default_factory=list, max_length=32)


class ExperimentDetail(ContractModel):
    """Index entry plus the metrics the source record actually contains."""

    schema_version: Literal["1.0"] = "1.0"
    experiment_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    request_id: str | None = Field(default=None, max_length=256)
    artifact_id: str | None = Field(default=None, max_length=256)
    scenario_id: str | None = Field(default=None, max_length=256)
    model_id: str | None = Field(default=None, max_length=512)
    runtime_id: str | None = Field(default=None, max_length=256)
    mode: str | None = Field(default=None, max_length=64)
    execution_status: ExperimentExecutionStatus
    raw_status: str = Field(max_length=64)
    data_source: ExperimentDataSource
    evidence_level: ExperimentEvidenceLevel
    created_at: str | None = None
    updated_at: str | None = None
    metrics_available: bool = False
    metrics: ExperimentMetrics | None = None
    limitations: list[str] = Field(default_factory=list, max_length=32)


class ExperimentCompareRequest(ContractModel):
    """Compare existing reports only; no new experiment is created."""

    schema_version: Literal["1.0"] = "1.0"
    run_ids: list[str] = Field(min_length=2, max_length=2)

    @field_validator("run_ids")
    @classmethod
    def unique_run_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("run_ids must be unique")
        return value


class ExperimentComparisonMetric(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(max_length=256)
    unit: str = Field(default="", max_length=64)
    values: dict[str, float]
    difference: float
    evidence_mode: Literal["synthetic", "observed", "measured"]


class ExperimentCompareResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    comparable: bool
    status: Literal["comparable", "not_comparable"]
    reasons: list[str] = Field(default_factory=list, max_length=32)
    run_ids: list[str] = Field(default_factory=list, max_length=8)
    metrics: list[ExperimentComparisonMetric] = Field(default_factory=list, max_length=64)
    limitations: list[str] = Field(default_factory=list, max_length=32)
