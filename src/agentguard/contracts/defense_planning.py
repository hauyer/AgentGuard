"""Versioned contract for evidence-constrained defense planning (v1.0).

Ranking factors keep unknown data as ``None`` instead of invented predictions;
every candidate carries a verification plan that stays unexecuted until a real
intervention executor and independent evaluation exist. A recommendation is
never an effectiveness claim.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class DefenseRankingFactors(ContractModel):
    """Transparent ranking inputs; missing measurements stay None."""

    schema_version: Literal["1.0"] = "1.0"
    risk_relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_sufficiency: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_effect: float | None = Field(default=None, ge=0.0, le=1.0)
    benign_utility_cost: float | None = Field(default=None, ge=0.0, le=1.0)
    execution_cost: float | None = Field(default=None, ge=0.0)
    scope_of_impact: Literal["unknown", "single_edge", "single_agent",
                             "multi_agent", "scenario_wide"] = "unknown"
    approval_required: bool = True
    reversibility: Literal["reversible", "partially_reversible",
                           "irreversible", "unknown"] = "unknown"
    execution_supported: bool = False
    unknown_fields: list[str] = Field(default_factory=list, max_length=16)


class DefenseVerificationPlan(ContractModel):
    """How a candidate would be verified; planned, never auto-executed."""

    schema_version: Literal["1.0"] = "1.0"
    verification_plan_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    treatment_group: str = Field(min_length=4, max_length=512)
    control_group: str = Field(min_length=4, max_length=512)
    fixed_factors: list[str] = Field(default_factory=list, max_length=16)
    risk_metrics: list[str] = Field(default_factory=list, max_length=16)
    utility_metrics: list[str] = Field(default_factory=list, max_length=16)
    repetitions: int = Field(default=2, ge=2, le=100)
    exit_conditions: list[str] = Field(default_factory=list, max_length=16)
    required_capabilities: list[str] = Field(default_factory=list, max_length=16)
    executability: Literal[
        "blocked_no_intervention_executor", "blocked_no_paired_replay",
        "blocked_no_independent_evaluation",
    ] = "blocked_no_intervention_executor"
    execution_status: Literal["not_executed"] = "not_executed"
    limitations: list[str] = Field(default_factory=list, max_length=16)


class GovernedDefenseCandidatePlan(ContractModel):
    """One candidate with its ranking factors and verification plan."""

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=64)
    target_ref: str = Field(min_length=1, max_length=256)
    policy_expression: str = Field(min_length=1, max_length=512)
    ranking: DefenseRankingFactors
    verification_plan: DefenseVerificationPlan


class GovernedDefensePlan(ContractModel):
    """Evidence-constrained defense plan bundle for one investigation."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    candidates: list[GovernedDefenseCandidatePlan] = Field(
        default_factory=list, max_length=64,
    )
    overall_execution_supported: bool = False
    limitations: list[str] = Field(default_factory=list, max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    plan_artifact_id: str | None = Field(default=None, max_length=256)
