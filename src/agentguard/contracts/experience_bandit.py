"""Public response contract for evidence-gated action ranking."""
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class ExperienceBanditActionScore(ContractModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    action_type: str
    posterior_alpha: float = Field(ge=0)
    posterior_beta: float = Field(ge=0)
    posterior_mean_reward: float = Field(ge=0, le=1)
    thompson_sample: float = Field(ge=0, le=1)
    reward_uncertainty: float = Field(ge=0)
    mean_execution_cost: float = Field(ge=0)
    cost_adjusted_sample: float
    verified_record_count: int = Field(ge=1)
    success_records: int = Field(ge=0)
    partial_records: int = Field(ge=0)
    failure_records: int = Field(ge=0)
    repetition_count: int = Field(ge=0)
    evidence_grades: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    policy_versions: list[str] = Field(default_factory=list)
    dataset_partitions: list[str] = Field(default_factory=list)
    execution_authorized: Literal[False] = False


class ExperienceBanditRecommendationReport(ContractModel):
    schema_version: Literal["0.1", "0.2"] = "0.2"
    algorithm: Literal["beta_bernoulli_thompson_sampling"] = "beta_bernoulli_thompson_sampling"
    prior: str = "Jeffreys Beta(0.5, 0.5)"
    seed: int
    scenario_id: str | None = None
    risk_type: str | None = None
    case_family: str | None = None
    target_partition: str = "held_out_family"
    recommendation_policy_version: str = "experience-thompson-v1"
    eligible_record_count: int = Field(ge=0)
    excluded_record_counts: dict[str, int] = Field(default_factory=dict)
    items: list[ExperienceBanditActionScore] = Field(default_factory=list)
    recommendation_only: Literal[True] = True
    execution_authorized: Literal[False] = False
    limitations: list[str] = Field(default_factory=list)
