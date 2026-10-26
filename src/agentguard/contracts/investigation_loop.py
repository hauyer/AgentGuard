"""Versioned contract for evidence-driven multi-round investigation (v1.0).

The loop composes existing read-only services and the standardized observation
workflow into a recorded, budgeted, stoppable investigation. `ACTION_EXECUTED`
means an authorized action actually ran (a read, or a supported audit
submission); recommendation-only steps are never recorded as interventions.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel

LoopActionType = Literal[
    "read_state", "read_evidence", "read_graph", "read_experiments",
    "read_experiences", "read_hypotheses", "submit_observation_audit",
    "plan_intervention",
]

LoopPhase = Literal[
    "CREATED", "EVIDENCE_COLLECTED", "HYPOTHESES_GENERATED", "ACTIONS_RANKED",
    "ACTION_APPROVED", "ACTION_EXECUTED", "EVIDENCE_UPDATED",
    "RUNNING", "STOPPED", "BLOCKED", "COMPLETED",
]

LoopActionStatus = Literal[
    "completed", "blocked", "failed", "unsupported", "skipped",
]

SelectionStrategy = Literal[
    "fixed_order", "risk_score", "information_value", "experience", "llm",
]


class LoopAction(ContractModel):
    """One executable investigation action offered to the strategy."""

    schema_version: Literal["1.0"] = "1.0"
    action_id: str = Field(min_length=1, max_length=128)
    action_type: LoopActionType
    requires: Literal["read", "audit", "intervention"] = "read"
    description: str = Field(min_length=1, max_length=512)
    evidence_need: str = Field(default="", max_length=512)
    cost_estimate: float = Field(default=1.0, ge=0.0)


class LoopDecisionRecord(ContractModel):
    """The durable record of one investigation round."""

    schema_version: Literal["1.0"] = "1.0"
    round_index: int = Field(ge=1)
    phase: LoopPhase
    selected_action: str = Field(min_length=1, max_length=128)
    candidate_actions: list[str] = Field(default_factory=list, max_length=32)
    selection_strategy: SelectionStrategy
    selection_reason: str = Field(min_length=1, max_length=1024)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    cost_estimate: float = Field(default=0.0, ge=0.0)
    actual_cost: float = Field(default=0.0, ge=0.0)
    result_status: LoopActionStatus
    new_evidence: bool = False
    stop_reason: str | None = Field(default=None, max_length=256)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InvestigationLoopBudget(ContractModel):
    """Upper-layer budget checked between actions and applied to supported calls."""

    schema_version: Literal["1.0"] = "1.0"
    max_actions: int = Field(default=8, ge=1, le=100)
    max_seconds: float = Field(default=300.0, gt=0.0, le=86400.0)
    max_model_calls: int = Field(default=4, ge=0, le=64)
    no_new_evidence_rounds: int = Field(default=2, ge=1, le=10)


class InvestigationLoopMetrics(ContractModel):
    """Investigation yield; tool-call counts are never reported as accuracy."""

    schema_version: Literal["1.0"] = "1.0"
    rounds_executed: int = Field(default=0, ge=0)
    steps_to_first_evidence: int | None = Field(default=None, ge=1)
    effective_action_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    blocked_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    unsupported_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    redundant_action_count: int = Field(default=0, ge=0)
    hypotheses_raised: int = Field(default=0, ge=0)
    evidence_refs_collected: int = Field(default=0, ge=0)
    model_calls_used: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)


class InvestigationLoopReport(ContractModel):
    """Final state of one multi-round investigation run."""

    schema_version: Literal["1.0"] = "1.0"
    investigation_id: str = Field(min_length=1, max_length=256)
    base_request_id: str = Field(min_length=1, max_length=256)
    strategy: SelectionStrategy
    strategy_degraded: bool = False
    strategy_degradation_note: str = Field(default="", max_length=512)
    phase: LoopPhase
    stop_reason: str = Field(min_length=1, max_length=256)
    decisions: list[LoopDecisionRecord] = Field(default_factory=list, max_length=100)
    budget: InvestigationLoopBudget
    metrics: InvestigationLoopMetrics
    created_audit_request_ids: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    report_artifact_id: str | None = Field(default=None, max_length=256)
