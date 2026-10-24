from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class RunTimelineEvent(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    sequence: int = Field(ge=0)
    stage: Literal[
        "created", "validated", "scenario_loaded", "observed", "detected",
        "planned", "policy_checked", "intervened", "evaluated", "defense_preview",
        "verification", "artifact_persisted", "experience_recorded", "cancelled",
        "completed", "partial", "failed"
    ]
    status: Literal["started", "completed", "skipped", "rejected", "failed", "info"]
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, str | int | float | bool] = Field(default_factory=dict)


class BudgetUsage(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    max_interventions: int = Field(ge=0)
    intervention_requests: int = Field(default=0, ge=0)
    intervention_attempts: int = Field(default=0, ge=0)
    observations_collected: int = Field(default=0, ge=0)
    findings_generated: int = Field(default=0, ge=0)
    max_agent_steps: int = Field(default=32, ge=1, le=100)
    agent_steps_used: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    max_runtime_seconds: int = Field(ge=1)
    runtime_budget_exceeded: bool = False
    max_model_calls: int = Field(default=64, ge=1)
    model_calls_used: int | None = Field(default=None, ge=0)
    max_model_tokens: int = Field(default=50000, ge=0)
    model_tokens_used: int | None = Field(default=None, ge=0)
    model_token_budget_exceeded: bool = False
    max_tool_calls: int = Field(default=40, ge=0)
    tool_calls_used: int | None = Field(default=None, ge=0)
    tool_call_budget_exceeded: bool = False
