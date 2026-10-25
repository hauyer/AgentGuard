"""Public contracts for the investigation-loop service layer (E2).

The public loop view is a projection of the persisted loop report artifact;
decision fields the artifact does not contain are reported as null, never
retrofitted. Creation is authorization-gated at the route layer (operator or
approval credential); the contract itself carries only the request shape.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from agentguard.contracts.base import ContractModel
from agentguard.contracts.investigation_loop import (
    SelectionStrategy,
)


class InvestigationLoopCreateRequest(ContractModel):
    """Creation request; the loop idempotency key makes retries safe."""

    schema_version: Literal["1.0"] = "1.0"
    base_request_id: str = Field(min_length=1, max_length=256)
    strategy: SelectionStrategy = "information_value"
    max_actions: int = Field(default=8, ge=1, le=100)
    max_seconds: float = Field(default=300.0, gt=0.0, le=3600.0)
    max_model_calls: int = Field(default=4, ge=0, le=64)
    no_new_evidence_rounds: int = Field(default=2, ge=1, le=10)
    data_partition: Literal[
        "training", "validation", "test", "held_out_family"
    ] = "held_out_family"
    authorization_ref: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def time_budget_bounded(self):
        if self.max_seconds > 600.0 and self.max_actions > 20:
            raise ValueError(
                "request exceeds the service-side loop budget envelope; lower "
                "max_actions or max_seconds"
            )
        return self


class LoopDecisionView(ContractModel):
    """One recorded round as the public decision record.

    ``evidence_refs_before/after`` and ``new_evidence_count`` come from the
    decision's recorded delta; older artifacts without a field stay null.
    """

    schema_version: Literal["1.0"] = "1.0"
    decision_id: str = Field(min_length=1, max_length=256)
    round_index: int = Field(ge=1)
    phase: str = Field(max_length=64)
    strategy: str = Field(max_length=64)
    candidate_actions: list[str] = Field(default_factory=list, max_length=32)
    selected_action_id: str = Field(min_length=1, max_length=128)
    selection_reason: str = Field(min_length=1, max_length=1024)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    evidence_refs_before: list[str] | None = None
    evidence_refs_after: list[str] | None = None
    new_evidence_count: int | None = Field(default=None, ge=0)
    cost_estimate: float = Field(default=0.0, ge=0.0)
    actual_cost: float = Field(default=0.0, ge=0.0)
    remaining_budget: dict[str, float | int | None] = Field(default_factory=dict)
    result_status: str = Field(max_length=32)
    new_evidence: bool = False
    stop_reason: str | None = Field(default=None, max_length=256)


class InvestigationLoopView(ContractModel):
    """Public loop summary with ID semantics kept distinct."""

    schema_version: Literal["1.0"] = "1.0"
    loop_id: str = Field(min_length=1, max_length=256)
    base_request_id: str = Field(min_length=1, max_length=256)
    strategy: str = Field(max_length=64)
    phase: str = Field(max_length=64)
    stop_reason: str | None = Field(default=None, max_length=256)
    report_artifact_id: str = Field(min_length=1, max_length=256)
    created_at: str | None = None
    updated_at: str | None = None
    rounds_executed: int | None = Field(default=None, ge=0)
    created_audit_request_ids: list[str] = Field(default_factory=list, max_length=100)
    budget: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list, max_length=32)


class InvestigationLoopListResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[InvestigationLoopView] = Field(default_factory=list, max_length=500)
    count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    next_offset: int | None = None


class LoopDecisionsResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    loop_id: str = Field(min_length=1, max_length=256)
    items: list[LoopDecisionView] = Field(default_factory=list, max_length=100)
    count: int = Field(ge=0)


class LoopEvidenceChangesResponse(ContractModel):
    """Evidence deltas across rounds; rounds without a change are omitted."""

    schema_version: Literal["1.0"] = "1.0"
    loop_id: str = Field(min_length=1, max_length=256)
    items: list[LoopDecisionView] = Field(default_factory=list, max_length=100)
    count: int = Field(ge=0)
    limitations: list[str] = Field(default_factory=list, max_length=16)


class LoopBudgetResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    loop_id: str = Field(min_length=1, max_length=256)
    budget: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    remaining: dict[str, float | int | None] = Field(default_factory=dict)
