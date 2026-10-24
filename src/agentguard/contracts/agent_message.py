from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from agentguard.contracts.base import ContractModel


class AgentMessage(ContractModel):
    """Typed, auditable message between role-separated AgentGuard agents."""

    schema_version: Literal["0.1"] = "0.1"
    message_id: str = Field(default_factory=lambda: f"agmsg-{uuid4().hex[:12]}")
    run_id: str
    sender_role: Literal[
        "supervisor", "trace_analyst", "causal_investigator",
        "defense_planner", "policy_engine", "runtime", "evaluator", "verifier", "memory_agent"
    ]
    recipient_role: Literal[
        "supervisor", "trace_analyst", "causal_investigator",
        "defense_planner", "policy_engine", "runtime", "evaluator", "verifier", "memory_agent"
    ]
    message_type: str
    payload_schema_version: str = "0.1"
    payload: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    deadline: datetime | None = None
    policy_context: dict[str, str | int | float | bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
