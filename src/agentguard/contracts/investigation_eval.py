"""Versioned contract for controlled investigation-strategy evaluation (v1.0).

Metrics are grouped and honestly scoped: risk-identification metrics require
independent, legally obtained labels and stay ``None`` without them; defense
metrics require real interventions and stay ``None`` until the kernel supports
them. Records capture what actually ran — blocked, failed and timed-out runs
count; nothing is pre-filled.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel
from agentguard.contracts.investigation_loop import (
    InvestigationLoopBudget,
    SelectionStrategy,
)


class StrategyRunRecord(ContractModel):
    """One executed evaluation run of one strategy."""

    schema_version: Literal["1.0"] = "1.0"
    experiment_id: str = Field(min_length=1, max_length=128)
    run_label: str = Field(min_length=1, max_length=128)
    strategy: SelectionStrategy
    repetition: int = Field(ge=1)
    base_request_id: str = Field(min_length=1, max_length=256)
    seed: int | None = None
    budget: InvestigationLoopBudget
    execution_status: Literal[
        "completed", "blocked", "failed", "timed_out", "skipped_not_configured",
    ]
    stop_reason: str | None = Field(default=None, max_length=256)
    rounds_executed: int = Field(default=0, ge=0)
    steps_to_first_evidence: int | None = Field(default=None, ge=1)
    effective_action_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    blocked_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    unsupported_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    redundant_action_count: int = Field(default=0, ge=0)
    evidence_refs_collected: int = Field(default=0, ge=0)
    hypotheses_raised: int = Field(default=0, ge=0)
    model_calls_used: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    strategy_degraded: bool = False
    loop_report_artifact_id: str | None = Field(default=None, max_length=256)
    limitations: list[str] = Field(default_factory=list, max_length=16)
    source_commit: str | None = Field(default=None, max_length=64)


class StrategyAggregate(ContractModel):
    """Aggregates over repetitions; uncertainty is reported, not hidden."""

    schema_version: Literal["1.0"] = "1.0"
    strategy: SelectionStrategy
    repetitions: int = Field(ge=0)
    completed_runs: int = Field(default=0, ge=0)
    blocked_runs: int = Field(default=0, ge=0)
    mean_rounds: float | None = None
    mean_steps_to_first_evidence: float | None = None
    mean_effective_action_ratio: float | None = None
    mean_redundant_actions: float | None = None
    mean_evidence_refs: float | None = None
    mean_elapsed_seconds: float | None = None
    degradation_rate: float | None = None
    notes: list[str] = Field(default_factory=list, max_length=16)


class StrategyComparisonReport(ContractModel):
    """Paired comparison of strategies over the same base investigations."""

    schema_version: Literal["1.0"] = "1.0"
    comparison_id: str = Field(min_length=1, max_length=128)
    base_request_ids: list[str] = Field(min_length=1, max_length=16)
    strategies: list[SelectionStrategy] = Field(min_length=1, max_length=8)
    repetitions: int = Field(ge=1, le=100)
    data_partition: Literal["training", "validation", "test", "held_out_family"]
    runs: list[StrategyRunRecord] = Field(default_factory=list, max_length=500)
    aggregates: list[StrategyAggregate] = Field(default_factory=list, max_length=8)
    # Scoped metric availability: absent capabilities keep metric groups null.
    risk_identification_metrics: None = None
    defense_effectiveness_metrics: None = None
    limitations: list[str] = Field(default_factory=list, max_length=32)
    source_commit: str | None = Field(default=None, max_length=64)
    source_tree_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_kind: Literal[
        "synthetic", "recorded_observational", "live_observational", "mixed", "unknown",
    ] = "unknown"
    declared_source_kinds: list[str] = Field(default_factory=list, max_length=16)
    independent_sample_count: int = Field(default=0, ge=0)
    technical_repetitions: int = Field(default=1, ge=1)
    actual_model_calls: int = Field(default=0, ge=0)
    runtime_version: str = Field(default="unknown", max_length=128)
    runtime_build_commit: str = Field(default="unknown", max_length=64)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    report_artifact_id: str | None = Field(default=None, max_length=256)
