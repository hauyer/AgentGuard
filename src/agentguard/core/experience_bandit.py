"""Evidence-gated, context-filtered Thompson ranking with explicit cost and uncertainty."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from agentguard.contracts.experience import ExperienceRecommendationPolicy, ExperienceRecord
from agentguard.contracts.experience_bandit import ExperienceBanditRecommendationReport

REWARD_BY_OUTCOME = {"success": 1.0, "partial": 0.5, "failure": 0.0}


def recommend_actions(
    records: list[ExperienceRecord],
    *,
    scenario_id: str | None = None,
    risk_type: str | None = None,
    case_family: str | None = None,
    target_partition: str = "held_out_family",
    model_version: str | None = None,
    policy_version: str | None = None,
    conflicted_experience_ids: set[str] | None = None,
    policy: ExperienceRecommendationPolicy | None = None,
    seed: int = 7,
    limit: int = 5,
) -> dict[str, Any]:
    """Rank action types using training-only, complete, non-conflicting records.

    A record contributes one outcome, never ``repetitions`` independent rewards. Test,
    held-out, same-family, expired, incomplete, synthetic, observational, legacy, and
    conflicting records are excluded. The ranking is advice only and never executes.
    """
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if target_partition not in {"training", "validation", "test", "held_out_family"}:
        raise ValueError("target_partition is invalid")
    policy = policy or ExperienceRecommendationPolicy(policy_version="experience-thompson-v1")
    conflicts = conflicted_experience_ids or set()
    grouped: dict[str, list[ExperienceRecord]] = defaultdict(list)
    excluded = {
        "context_mismatch": 0,
        "same_case_family": 0,
        "not_training_partition": 0,
        "not_verified": 0,
        "recommendation_not_enabled": 0,
        "evidence_not_interventional": 0,
        "outcome_inconclusive": 0,
        "missing_evidence_refs": 0,
        "missing_evaluation_metrics": 0,
        "missing_benign_utility": 0,
        "missing_execution_cost": 0,
        "missing_reproducibility_provenance": 0,
        "source_not_completed_warden_run": 0,
        "evidence_provenance_unverified": 0,
        "below_policy_minimum_repetitions": 0,
        "legacy_schema_requires_revalidation": 0,
        "expired": 0,
        "conflicting_evidence": 0,
    }
    now = datetime.now(UTC)
    eligible: list[ExperienceRecord] = []
    for record in records:
        if (scenario_id and record.scenario_id != scenario_id) or (risk_type and record.risk_type != risk_type):
            excluded["context_mismatch"] += 1
            continue
        if case_family and record.case_family == case_family:
            excluded["same_case_family"] += 1
            continue
        reasons = record.reward_ineligibility_reasons(
            now=now, conflicted=record.experience_id in conflicts
        )
        # Count each reason so callers can see why a record was quarantined.
        for reason in reasons:
            if reason == "not_training_partition":
                excluded["not_training_partition"] += 1
            elif reason == "not_verified":
                excluded["not_verified"] += 1
            elif reason == "recommendation_not_enabled":
                excluded["recommendation_not_enabled"] += 1
            elif reason == "evidence_not_interventional":
                excluded["evidence_not_interventional"] += 1
            elif reason == "outcome_inconclusive":
                excluded["outcome_inconclusive"] += 1
            elif reason == "missing_evidence_refs":
                excluded["missing_evidence_refs"] += 1
            elif reason == "missing_evaluation_metrics":
                excluded["missing_evaluation_metrics"] += 1
            elif reason == "missing_benign_utility":
                excluded["missing_benign_utility"] += 1
            elif reason == "missing_execution_cost":
                excluded["missing_execution_cost"] += 1
            elif reason in {"missing_audit_run_id", "unknown_model_or_policy_version", "missing_verified_at", "missing_reproducibility_hash", "not_repeated"}:
                excluded["missing_reproducibility_provenance"] += 1
            elif reason == "legacy_schema_requires_revalidation":
                excluded["legacy_schema_requires_revalidation"] += 1
            elif reason == "expired":
                excluded["expired"] += 1
            elif reason == "conflicting_evidence":
                excluded["conflicting_evidence"] += 1
            elif reason == "source_not_completed_warden_run":
                excluded["source_not_completed_warden_run"] += 1
            elif reason == "evidence_provenance_unverified":
                excluded["evidence_provenance_unverified"] += 1
        if model_version and record.model_version != model_version:
            excluded["context_mismatch"] += 1
            reasons.append("model_version_mismatch")
        if policy_version and record.policy_version != policy_version:
            excluded["context_mismatch"] += 1
            reasons.append("policy_version_mismatch")
        if record.repetitions < policy.minimum_repetitions:
            excluded["below_policy_minimum_repetitions"] += 1
            reasons.append("below_policy_minimum_repetitions")
        if not reasons:
            eligible.append(record)
            grouped[record.action_type].append(record)

    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for action_type, action_records in grouped.items():
        successes = sum(item.outcome == "success" for item in action_records)
        failures = sum(item.outcome == "failure" for item in action_records)
        partials = sum(item.outcome == "partial" for item in action_records)
        alpha = 0.5 + successes + 0.5 * partials
        beta = 0.5 + failures + 0.5 * partials
        sample = rng.betavariate(alpha, beta)
        mean = alpha / (alpha + beta)
        variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1))
        uncertainty = variance**0.5
        mean_cost = sum(item.execution_cost or 0.0 for item in action_records) / len(action_records)
        normalized_cost = mean_cost / (mean_cost + policy.cost_scale)
        cost_adjusted_sample = sample - policy.cost_penalty * normalized_cost
        rows.append({
            "action_type": action_type,
            "posterior_alpha": round(alpha, 6),
            "posterior_beta": round(beta, 6),
            "posterior_mean_reward": round(mean, 6),
            "thompson_sample": round(sample, 6),
            "reward_uncertainty": round(uncertainty, 6),
            "mean_execution_cost": round(mean_cost, 6),
            "cost_adjusted_sample": round(cost_adjusted_sample, 6),
            "verified_record_count": len(action_records),
            "success_records": successes,
            "partial_records": partials,
            "failure_records": failures,
            "repetition_count": sum(item.repetitions for item in action_records),
            "evidence_grades": sorted({item.evidence_grade for item in action_records}),
            "evidence_refs": sorted({ref for item in action_records for ref in item.evidence_refs}),
            "policy_versions": sorted({item.policy_version for item in action_records}),
            "dataset_partitions": sorted({item.dataset_partition for item in action_records}),
            "execution_authorized": False,
        })
    rows.sort(key=lambda row: (row["cost_adjusted_sample"], row["posterior_mean_reward"]), reverse=True)
    report = ExperienceBanditRecommendationReport.model_validate({
        "algorithm": policy.algorithm,
        "prior": "Jeffreys Beta(0.5, 0.5)",
        "seed": seed,
        "scenario_id": scenario_id,
        "risk_type": risk_type,
        "case_family": case_family,
        "target_partition": target_partition,
        "recommendation_policy_version": policy.policy_version,
        "eligible_record_count": len(eligible),
        "excluded_record_counts": excluded,
        "items": rows[:limit],
        "recommendation_only": True,
        "execution_authorized": False,
        "limitations": [
            "Only complete, verified, recommendation-enabled training records with interventional evidence are reward-eligible.",
            "Test and held-out records, same-family records, expired records, and unresolved evidence conflicts are excluded.",
            "One record contributes one outcome observation; repetitions are reported but not treated as independent rewards.",
            "Execution cost adjusts the sampled score using the selected immutable recommendation-policy version.",
            "Posterior uncertainty and score are decision-support summaries, not calibrated probabilities or causal estimates.",
            "Selection bias and context mismatch can invalidate recommendations; evaluate prospectively before deployment.",
        ],
    })
    return report.model_dump(mode="json")
