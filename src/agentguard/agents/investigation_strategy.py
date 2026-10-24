"""Deterministic, auditable candidate ranking policies for one investigation round."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentguard.contracts.experience import ExperienceRecord
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


def rank_findings(
    findings: list[DetectionFinding],
    *,
    strategy: InvestigationStrategy,
    actions: list[InvestigationAction] | None = None,
    hypotheses: list[InvestigationHypothesis] | None = None,
    experience_records: list[ExperienceRecord] | None = None,
    scenario_id: str | None = None,
    max_candidates: int = 32,
) -> tuple[list[RankedFinding], str | None]:
    """Rank a bounded candidate prefix and return any explicit strategy fallback."""
    candidates = list(findings[:max_candidates])
    if not candidates:
        return [], None

    effective_strategy = "information_value" if strategy == "llm" else strategy
    fallback_reason = None
    if effective_strategy == "experience":
        has_eligible = any(
            record.verification_status == "verified"
            and record.allow_auto_recommendation
            and record.evidence_grade in {"interventional", "causal_validated"}
            and (scenario_id is None or record.scenario_id == scenario_id)
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
            if (
                record.verification_status != "verified"
                or not record.allow_auto_recommendation
                or record.evidence_grade not in {"interventional", "causal_validated"}
                or (scenario_id is not None and record.scenario_id != scenario_id)
            ):
                continue
            for finding in candidates:
                if record.risk_type == finding.risk_type:
                    eligible_by_finding[finding.finding_id].append(record)

    scored: list[tuple[int, DetectionFinding, float, str, int]] = []
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
            rewards = {"success": 1.0, "partial": 0.5, "failure": 0.0, "inconclusive": 0.0}
            mean_outcome = (
                sum(rewards[item.outcome] for item in experience) / len(experience)
                if experience
                else 0.0
            )
            score = mean_outcome + min(len(experience), 10) * 0.01 + finding.score * 0.001
            rationale = (
                f"Matched {len(experience)} verified, recommendation-enabled interventional records "
                "for this scenario and risk; ranking is advisory, not a causal estimate."
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
        )
        for rank, (_index, finding, score, rationale, count) in enumerate(ordered, start=1)
    ]
    return result, fallback_reason
