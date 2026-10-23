"""Evidence-gated Thompson sampling over verified experience records.

This module ranks action *candidates* only. It never executes an action, never treats
unverified or synthetic-only records as reward evidence, and does not claim causal
validity merely because a record is marked verified.
"""
from __future__ import annotations

from collections import defaultdict
import random
from typing import Any

from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.experience_bandit import ExperienceBanditRecommendationReport


REWARD_BY_OUTCOME = {
    "success": 1.0,
    "partial": 0.5,
    "failure": 0.0,
}


def recommend_actions(
    records: list[ExperienceRecord],
    *,
    scenario_id: str | None = None,
    risk_type: str | None = None,
    seed: int = 7,
    limit: int = 5,
) -> dict[str, Any]:
    """Return evidence-gated action ranking using Beta-Bernoulli Thompson sampling.

    One record contributes one outcome observation regardless of ``repetitions``.
    This avoids treating within-record repeated trials as independent records when
    their individual outcomes are not available.
    """
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    grouped: dict[str, list[ExperienceRecord]] = defaultdict(list)
    excluded = {
        "not_verified": 0,
        "not_enabled": 0,
        "synthetic_or_observational": 0,
        "inconclusive": 0,
        "context_mismatch": 0,
    }
    eligible_count = 0
    for record in records:
        if scenario_id and record.scenario_id != scenario_id:
            excluded["context_mismatch"] += 1
            continue
        if risk_type and record.risk_type != risk_type:
            excluded["context_mismatch"] += 1
            continue
        if record.verification_status != "verified":
            excluded["not_verified"] += 1
            continue
        if not record.allow_auto_recommendation:
            excluded["not_enabled"] += 1
            continue
        if record.evidence_grade not in {"interventional", "causal_validated"}:
            excluded["synthetic_or_observational"] += 1
            continue
        if record.outcome == "inconclusive":
            excluded["inconclusive"] += 1
            continue
        eligible_count += 1
        grouped[record.action_type].append(record)

    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for action_type, action_records in grouped.items():
        successes = sum(r.outcome == "success" for r in action_records)
        failures = sum(r.outcome == "failure" for r in action_records)
        partials = sum(r.outcome == "partial" for r in action_records)
        # Jeffreys prior: Beta(0.5, 0.5). Partial outcomes add half success and half failure.
        alpha = 0.5 + successes + 0.5 * partials
        beta = 0.5 + failures + 0.5 * partials
        sampled_reward = rng.betavariate(alpha, beta)
        mean_reward = alpha / (alpha + beta)
        rows.append({
            "action_type": action_type,
            "posterior_alpha": round(alpha, 6),
            "posterior_beta": round(beta, 6),
            "posterior_mean_reward": round(mean_reward, 6),
            "thompson_sample": round(sampled_reward, 6),
            "verified_record_count": len(action_records),
            "success_records": successes,
            "partial_records": partials,
            "failure_records": failures,
            "repetition_count": sum(r.repetitions for r in action_records),
            "evidence_grades": sorted({r.evidence_grade for r in action_records}),
            "evidence_refs": sorted({ref for r in action_records for ref in r.evidence_refs}),
            "execution_authorized": False,
        })
    rows.sort(key=lambda row: (row["thompson_sample"], row["posterior_mean_reward"]), reverse=True)
    report = ExperienceBanditRecommendationReport.model_validate({
        "algorithm": "beta_bernoulli_thompson_sampling",
        "prior": "Jeffreys Beta(0.5, 0.5)",
        "seed": seed,
        "scenario_id": scenario_id,
        "risk_type": risk_type,
        "eligible_record_count": eligible_count,
        "excluded_record_counts": excluded,
        "items": rows[:limit],
        "recommendation_only": True,
        "execution_authorized": False,
        "limitations": [
            "Only verified, explicitly recommendation-enabled interventional records are eligible.",
            "One record contributes one outcome observation; repetitions are reported but not treated as independent outcomes.",
            "The posterior is a decision-support heuristic, not a causal estimate or calibrated success probability.",
            "Action ranking does not grant execution authority; policy and runtime capability checks remain mandatory.",
            "Selection bias and context mismatch can invalidate recommendations; validate prospectively before deployment.",
        ],
    })
    return report.model_dump(mode="json")
