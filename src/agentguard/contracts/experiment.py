from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from agentguard.contracts.base import ContractModel


class ExperimentBudget(ContractModel):
    max_interventions: int = Field(default=1, ge=0, le=100)
    max_agent_steps: int = Field(default=32, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=0, le=100000)
    max_model_calls: int = Field(default=64, ge=1, le=100000)
    max_runtime_seconds: int = Field(default=120, ge=1, le=86400)
    max_model_tokens: int = Field(default=50000, ge=0, le=100000000)
    max_output_tokens_per_call: int = Field(default=384, ge=1, le=65536)


class ExperimentRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    experiment_id: str = Field(default_factory=lambda: f"exp-{uuid4().hex[:12]}")
    scenario_id: str = Field(min_length=1, max_length=128)
    case_family: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_partition: Literal["training", "validation", "test", "held_out_family"] = "held_out_family"
    model_version: str | None = Field(default=None, min_length=1, max_length=128)
    experience_record_policy_version: str | None = Field(default=None, min_length=1, max_length=64)
    recommendation_policy_version: str | None = Field(default=None, min_length=1, max_length=64)
    goal: str = Field(default="investigate unauthorized information flow", max_length=1024)
    investigation_strategy: Literal[
        "fixed_order", "risk_score", "information_value", "experience", "llm"
    ] = "information_value"
    max_agent_steps: int = Field(default=32, ge=1, le=100)
    max_interventions: int = Field(default=1, ge=0, le=100)
    max_runtime_seconds: int = Field(default=120, ge=1, le=86400)
    max_tool_calls: int = Field(default=40, ge=0, le=100000)
    max_model_calls: int = Field(default=64, ge=1, le=100000)
    max_output_tokens_per_call: int = Field(default=384, ge=1, le=65536)
    max_model_tokens: int = Field(default=50000, ge=0, le=100000000)
    runtime_backend: Literal["mock", "stegopot", "custom"] = "mock"
    auto_intervene: bool = True
    seed: int = 7
    families: int = Field(default=1, ge=1, le=10000)
    policy_profile: Literal["isolated_research", "synthetic_demo", "read_only"] = "isolated_research"
    budget: ExperimentBudget | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def flatten_nested_budget(cls, values):
        if not isinstance(values, dict) or values.get("budget") is None:
            return values
        data = dict(values)
        budget = data["budget"]
        if isinstance(budget, ExperimentBudget):
            budget_values = budget.model_dump(exclude_unset=True)
        elif isinstance(budget, dict):
            budget_values = dict(budget)
        else:
            return values
        for name, value in budget_values.items():
            if name in data and data[name] != value:
                raise ValueError(f"conflicting top-level and budget values for {name}")
            data.setdefault(name, value)
        return data
