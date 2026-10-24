"""Projection of the durable workflow journal into public timeline events.

Only ``warden_workflow_events`` rows are used. The projector maps journal
transitions to phase/event vocabulary; it never invents steps that the
lifecycle service did not record. Detail payloads are read through an
allowlist so unexpected journal fields cannot leak into responses.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentguard.contracts.investigation_timeline import (
    InvestigationTimelineEvent,
    InvestigationTimelineResponse,
    TimelineEventStatus,
    TimelineEventType,
    TimelinePhase,
)
from agentguard.services.warden_workflow_store import WardenWorkflowStore

_PHASE_BY_STATUS: dict[str, TimelinePhase] = {
    "created": "workflow_created",
    "preflighting": "validation",
    "plan_submitting": "plan_creation",
    "plan_created": "plan_creation",
    "run_submitting": "run_submission",
    "submission_uncertain": "run_submission",
    "run_submitted": "run_submission",
    "polling": "run_monitoring",
    "timed_out": "run_monitoring",
    "interrupted": "recovery",
    "finished": "completion",
    "blocked": "blocked",
    "cancelled": "cancellation",
}

_KNOWN_DETAIL_KEYS = frozenset({
    "operation", "is_retry", "is_recovery", "recovered", "reconciled",
    "plan_id", "plan_status", "run_id", "run_status", "result_status",
    "remote_status_from", "remote_status_to", "error_code", "exception_type",
    "external_submission_may_have_succeeded", "recovered_from",
})


def _bounded_text(value: Any, limit: int = 128) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


class InvestigationTimelineService:
    """Read-only timeline projection over ``WardenWorkflowStore``."""

    def __init__(self, workflows: WardenWorkflowStore) -> None:
        self.workflows = workflows

    def timeline(
        self, request_id: str, *, after_event_id: int | None = None
    ) -> InvestigationTimelineResponse | None:
        """Return None when the workflow does not exist (caller maps to 404)."""
        entry = self.workflows.get(request_id)
        if entry is None:
            return None
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        entry_plan_id = _bounded_text((context.get("plan") or {}).get("plan_id"))
        entry_run_id = _bounded_text((context.get("run") or {}).get("run_id"))
        limitations: list[str] = []
        events = self.workflows.events(request_id)
        projected: list[InvestigationTimelineEvent] = []
        for row in events:
            if after_event_id is not None and row["event_id"] <= after_event_id:
                continue
            detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
            to_status = str(row.get("to_status") or "unknown")
            from_status = row.get("from_status")
            plan_id = _bounded_text(detail.get("plan_id")) or entry_plan_id
            run_id = _bounded_text(detail.get("run_id")) or entry_run_id
            event_type = self._event_type(from_status, to_status, detail)
            status = self._status(to_status, detail)
            reason_code = (
                _bounded_text(detail.get("error_code"), 128)
                or _bounded_text(detail.get("exception_type"), 128)
            )
            projected.append(InvestigationTimelineEvent(
                event_id=row["event_id"],
                request_id=request_id,
                event_type=event_type,
                phase=_PHASE_BY_STATUS.get(to_status, "unknown"),
                status=status,
                occurred_at=str(row.get("created_at") or ""),
                source="warden_workflow_journal",
                related_plan_id=plan_id,
                related_run_id=run_id,
                reason_code=reason_code,
                message=self._message(from_status, to_status, detail),
            ))
        if not events:
            limitations.append(
                "The journal has no recorded events for this workflow yet; "
                "no timeline steps were synthesized."
            )
        if after_event_id is not None and not projected:
            limitations.append(
                f"No new events after event_id {after_event_id}."
            )
        last_event_id = events[-1]["event_id"] if events else None
        return InvestigationTimelineResponse(
            request_id=request_id,
            available=True,
            workflow_status=str(entry.get("workflow_status") or "unknown"),
            items=projected,
            count=len(projected),
            next_event_id=last_event_id,
            limitations=limitations,
        )

    @staticmethod
    def _event_type(
        from_status: Any, to_status: str, detail: Mapping[str, Any]
    ) -> TimelineEventType:
        operation = detail.get("operation")
        if from_status is None and to_status == "created":
            return "workflow_created"
        if operation == "cancel_local_pending" or to_status == "cancelled":
            return "cancellation"
        if detail.get("recovered") or detail.get("is_recovery") or to_status == "interrupted":
            return "recovery"
        if detail.get("reconciled"):
            return "reconciliation"
        if "remote_status_to" in detail:
            return "run_status_change"
        if operation == "process_workflow":
            return "processing_attempt"
        if operation == "create_audit_plan" or to_status in {"plan_submitting", "plan_created"}:
            return "plan_submission"
        if operation == "start_run" or to_status in {
            "run_submitting", "run_submitted", "submission_uncertain",
        }:
            return "run_submission"
        if to_status == "preflighting":
            return "validation"
        if "exception_type" in detail:
            return "interruption"
        if to_status == "polling" or to_status == "timed_out":
            return "run_monitoring"
        return "state_transition"

    @staticmethod
    def _status(to_status: str, detail: Mapping[str, Any]) -> TimelineEventStatus:
        if detail.get("external_submission_may_have_succeeded") is True:
            return "ambiguous"
        if to_status == "blocked":
            return "blocked"
        if "exception_type" in detail:
            return "failed"
        return "completed"

    @staticmethod
    def _message(
        from_status: Any, to_status: str, detail: Mapping[str, Any]
    ) -> str:
        origin = str(from_status) if from_status else "start"
        parts = [f"Journal transition {origin} -> {to_status}"]
        for key in sorted(_KNOWN_DETAIL_KEYS & set(detail)):
            value = detail[key]
            if isinstance(value, (str, int, float, bool)):
                parts.append(f"{key}={value}")
        return "; ".join(parts)[:1024]
