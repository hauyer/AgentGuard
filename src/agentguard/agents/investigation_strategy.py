"""Deterministic, auditable candidate ranking policies for one investigation round."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentguard.contracts.experience import ExperienceRecommendationPolicy, ExperienceRecord
from agentguard.contracts.hypothesis import InvestigationAction, InvestigationHypothesis
from agentguard.contracts.observation import DetectionFinding

InvestigationStrategy = Literal[
    "fixed_order", "risk_score", "information_value", "experience", "llm"
]

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class RankedFinding:
    finding: DetectionFinding
    rank: int
    score: float
    rationale: str
    eligible_experience_count: int = 0
    reward_uncertainty: float = 0.0
    mean_execution_cost: float = 0.0
    recommendation_policy_version: str | None = None


def rank_findings(
    findings: list[DetectionFinding],
    *,
    strategy: InvestigationStrategy,
    actions: list[InvestigationAction] | None = None,
    hypotheses: list[InvestigationHypothesis] | None = None,
    experience_records: list[ExperienceRecord] | None = None,
    scenario_id: str | None = None,
    case_family: str | None = None,
    dataset_partition: str = "held_out_family",
    experience_policy: ExperienceRecommendationPolicy | None = None,
    conflicted_experience_ids: set[str] | None = None,
    max_candidates: int = 32,
) -> tuple[list[RankedFinding], str | None]:
    """Rank a bounded candidate prefix and return any explicit strategy fallback."""
    candidates = list(findings[:max_candidates])
    if not candidates:
        return [], None

    effective_strategy = "information_value" if strategy == "llm" else strategy
    fallback_reason = None
    experience_policy = experience_policy or ExperienceRecommendationPolicy(
        policy_version="experience-thompson-v1"
    )
    conflicts = conflicted_experience_ids or set()

    def eligible_record(record: ExperienceRecord, finding: DetectionFinding | None = None) -> bool:
        if scenario_id is not None and record.scenario_id != scenario_id:
            return False
        if case_family is not None and record.case_family == case_family:
            return False
        if finding is not None and record.risk_type != finding.risk_type:
            return False
        # Runtime investigations may target validation/test data, but the memory source
        # is always the training partition. No evaluation partition can supply rewards.
        if dataset_partition not in {"training", "validation", "test", "held_out_family"}:
            return False
        return not record.reward_ineligibility_reasons(
            conflicted=record.experience_id in conflicts
        )

    if effective_strategy == "experience":
        has_eligible = any(
            eligible_record(record)
            and any(record.risk_type == finding.risk_type for finding in candidates)
            for record in (experience_records or [])
        )
        if not has_eligible:
            effective_strategy = "risk_score"
            fallback_reason = "no_eligible_verified_experience_fallback_to_risk_score"

    hypothesis_finding = {
        item.hypothesis_id: item.finding_id for item in (hypotheses or [])
    }
    information_score: dict[str, float] = {}
    for action in actions or []:
        finding_id = hypothesis_finding.get(action.hypothesis_id)
        if finding_id is not None:
            information_score[finding_id] = max(
                information_score.get(finding_id, 0.0), action.utility_score
            )

    eligible_by_finding: dict[str, list[ExperienceRecord]] = {
        finding.finding_id: [] for finding in candidates
    }
    if effective_strategy == "experience":
        for record in experience_records or []:
            if not eligible_record(record):
                continue
            for finding in candidates:
                if record.risk_type == finding.risk_type:
                    eligible_by_finding[finding.finding_id].append(record)

    scored: list[tuple[int, DetectionFinding, float, str, int]] = []
    metadata: dict[str, tuple[float, float]] = {}
    for index, finding in enumerate(candidates):
        experience = eligible_by_finding[finding.finding_id]
        if effective_strategy == "fixed_order":
            score = float(-index)
            rationale = "Candidate order is preserved from the detector output."
        elif effective_strategy == "risk_score":
            severity = _SEVERITY_RANK[finding.severity]
            score = severity + finding.score
            rationale = (
                f"Ranked by severity tier ({finding.severity}) and uncalibrated screening score."
            )
        elif effective_strategy == "experience":
            successes = sum(item.outcome == "success" for item in experience)
            failures = sum(item.outcome == "failure" for item in experience)
            partials = sum(item.outcome == "partial" for item in experience)
            alpha = 0.5 + successes + 0.5 * partials
            beta = 0.5 + failures + 0.5 * partials
            mean_outcome = alpha / (alpha + beta) if experience else 0.0
            variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1)) if experience else 0.0
            uncertainty = variance**0.5
            mean_cost = (
                sum(item.execution_cost or 0.0 for item in experience) / len(experience)
                if experience else 0.0
            )
            normalized_cost = mean_cost / (mean_cost + experience_policy.cost_scale) if experience else 0.0
            score = mean_outcome - experience_policy.cost_penalty * normalized_cost
            metadata[finding.finding_id] = (uncertainty, mean_cost)
            rationale = (
                f"Matched {len(experience)} complete training records for this scenario/risk; "
                f"posterior uncertainty={uncertainty:.4f}, mean execution cost={mean_cost:.4f}, "
                f"cost penalty={experience_policy.cost_penalty:.3f} under "
                f"{experience_policy.policy_version}; advisory ranking only."
            )
        else:
            action_score = information_score.get(finding.finding_id, 0.0)
            evidence_tiebreak = min(len(set(finding.evidence_refs)), 20) * 0.001
            score = action_score + evidence_tiebreak + finding.score * 0.0001
            rationale = (
                f"Ranked by best supported action utility ({action_score:.6f}), then distinct "
                "evidence-reference count; information value is a heuristic."
            )
        scored.append((index, finding, score, rationale, len(experience)))

    if effective_strategy == "fixed_order":
        ordered = scored
    else:
        ordered = sorted(scored, key=lambda item: (-item[2], item[0]))
    result = [
        RankedFinding(
            finding=finding,
            rank=rank,
            score=score,
            rationale=rationale,
            eligible_experience_count=count,
            reward_uncertainty=metadata.get(finding.finding_id, (0.0, 0.0))[0],
            mean_execution_cost=metadata.get(finding.finding_id, (0.0, 0.0))[1],
            recommendation_policy_version=(experience_policy.policy_version if effective_strategy == "experience" else None),
        )
        for rank, (_index, finding, score, rationale, count) in enumerate(ordered, start=1)
    ]
    return result, fallback_reason
