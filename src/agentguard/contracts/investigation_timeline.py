"""Versioned public contract for investigation lifecycle timelines (v1.0).

Timeline events are strict projections of the durable workflow journal
(``warden_workflow_events``). No event is synthesized for steps the journal
never recorded; absence stays visible as an empty list plus the workflow's
current state.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel

TimelineEventType = Literal[
    "workflow_created", "validation", "plan_submission", "run_submission",
    "run_status_change", "run_monitoring", "recovery", "reconciliation",
    "cancellation", "processing_attempt", "state_transition", "interruption",
]

TimelinePhase = Literal[
    "workflow_created", "validation", "plan_creation", "run_submission",
    "run_monitoring", "completion", "blocked", "cancellation", "recovery",
    "unknown",
]

TimelineEventStatus = Literal["completed", "failed", "blocked", "ambiguous", "info"]


class InvestigationTimelineEvent(ContractModel):
    """One durable journal transition projected for the application layer."""

    schema_version: Literal["1.0"] = "1.0"
    event_id: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=256)
    event_type: TimelineEventType
    phase: TimelinePhase
    status: TimelineEventStatus
    occurred_at: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=128)
    related_plan_id: str | None = Field(default=None, max_length=256)
    related_run_id: str | None = Field(default=None, max_length=256)
    reason_code: str | None = Field(default=None, max_length=128)
    message: str = Field(min_length=1, max_length=1024)


class InvestigationTimelineResponse(ContractModel):
    """Timeline view for one investigation workflow."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=256)
    available: bool
    workflow_status: str = Field(max_length=64)
    items: list[InvestigationTimelineEvent] = Field(default_factory=list, max_length=2000)
    count: int = Field(ge=0)
    next_event_id: int | None = None
    limitations: list[str] = Field(default_factory=list, max_length=32)
