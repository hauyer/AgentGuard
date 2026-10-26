"""Upper-layer workflow state projection and journal transition rules."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class WorkflowState(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    READY = "ready"
    PLAN_SUBMITTING = "plan_submitting"
    PLANNED = "planned"
    BLOCKED = "blocked"
    RUN_SUBMITTING = "run_submitting"
    SUBMISSION_UNCERTAIN = "submission_uncertain"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class WorkflowTransitionError(ValueError):
    """A requested journal transition would regress or overwrite a terminal result."""


TERMINAL_JOURNAL_STATES = frozenset({"finished", "completed", "failed", "blocked", "cancelled"})
_TRANSITIONS = {
    # Old journal imports can start with a previously recorded checkpoint.
    "created": {"preflighting", "plan_submitting", "plan_created", "run_submitting",
                "submission_uncertain", "run_submitted", "polling", "interrupted",
                "finished", "blocked", "cancelled"},
    "preflighting": {"plan_submitting", "finished", "blocked", "interrupted", "cancelled"},
    "plan_submitting": {"plan_created", "submission_uncertain", "unknown"},
    "plan_created": {"run_submitting", "run_submitted", "finished", "interrupted", "unknown"},
    "run_submitting": {"run_submitted", "submission_uncertain", "unknown"},
    "submission_uncertain": {"run_submitted", "unknown"},
    "run_submitted": {"polling", "finished", "timed_out", "unknown"},
    "polling": {"run_submitted", "finished", "timed_out", "unknown"},
    "timed_out": {"run_submitted", "polling", "finished", "unknown"},
    "interrupted": {"preflighting", "plan_created", "run_submitted", "polling", "finished", "unknown"},
    # Unknown is a reconciliation checkpoint, not a terminal outcome. A later
    # GET of the same known remote ID may reveal a documented state.
    "unknown": {"unknown", "plan_created", "run_submitted", "finished"},
    **{state: set() for state in TERMINAL_JOURNAL_STATES},
}


def validate_transition(previous: str, following: str) -> None:
    if previous not in _TRANSITIONS or following not in _TRANSITIONS:
        raise WorkflowTransitionError("Unknown workflow journal state")
    if previous != following and following not in _TRANSITIONS[previous]:
        raise WorkflowTransitionError(f"Workflow transition {previous} -> {following} is not allowed")


def kernel_to_workflow_state(status: Any) -> WorkflowState:
    """Map only documented remote plan/run states; unknown values stay unknown."""
    if isinstance(status, str) and status in {
        "planned", "blocked", "queued", "running", "completed", "failed", "cancelled"
    }:
        return WorkflowState(status)
    return WorkflowState.UNKNOWN


def workflow_to_kernel_status(state: WorkflowState) -> str | None:
    """Return the corresponding remote status, never inventing a WARDEN state."""
    value = kernel_to_workflow_state(state.value)
    return value.value if value is not WorkflowState.UNKNOWN else None


def project_workflow_state(
    journal_status: str, context: Mapping[str, Any], result: Mapping[str, Any] | None,
) -> WorkflowState:
    if journal_status == "finished":
        return kernel_to_workflow_state((result or {}).get("status"))
    if journal_status in {"run_submitted", "polling"}:
        return kernel_to_workflow_state((context.get("run") or {}).get("status"))
    aliases = {
        "preflighting": "validating", "plan_created": "planned", "interrupted": "unknown",
    }
    try:
        return WorkflowState(aliases.get(journal_status, journal_status))
    except ValueError:
        return WorkflowState.UNKNOWN
