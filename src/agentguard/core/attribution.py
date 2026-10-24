"""Secret decodability, causal mediation and path-shift summaries.

The functions calculate summaries from supplied arrays. They cannot verify that arrays
were produced by a valid activation-patching experiment; causal interpretation remains
conditional on experimental design and artifact provenance.
"""
from __future__ import annotations

import math
from statistics import mean

from agentguard.contracts.attribution import (
    AttributionNodeAnalysisReport,
    AttributionNodeAnalysisRequest,
    PathShiftAnalysisReport,
    PathShiftAnalysisRequest,
)
from agentguard.core.statistics import clustered_paired_effect_summary


def analyze_attribution_node(request: AttributionNodeAnalysisRequest) -> AttributionNodeAnalysisReport:
    # The mediation contribution is baseline recovery minus recovery after intervention.
    # Paired summary's effect is treatment - control, so intervention is control and baseline is treatment.
    recovery = clustered_paired_effect_summary(
        request.intervened_secret_recovery,
        request.baseline_secret_recovery,
        request.case_families,
        seed=request.seed,
        bootstrap_draws=request.bootstrap_draws,
        permutation_draws=request.permutation_draws,
    )
    recovery["baseline_secret_recovery_mean"] = recovery.pop("treatment_mean")
    recovery["intervened_secret_recovery_mean"] = recovery.pop("control_mean")
    recovery["mean_baseline_minus_intervened"] = recovery.pop("mean_treatment_minus_control")
    recovery["median_family_baseline_minus_intervened"] = recovery.pop("median_family_effect")
    recovery["mediation_contribution_baseline_minus_intervened"] = recovery["mean_baseline_minus_intervened"]
    recovery["estimand"] = "equal-weight case-family mean baseline recovery minus intervened recovery"
    recovery["interpretation"] = (
        "Positive values indicate lower secret recovery after the supplied intervention; "
        "this is evidence about the candidate node only if the intervention was valid and matched."
    )
    collusion = None
    if request.baseline_collusion_success is not None and request.intervened_collusion_success is not None:
        collusion = clustered_paired_effect_summary(
            request.intervened_collusion_success,
            request.baseline_collusion_success,
            request.case_families,
            seed=request.seed + 11,
            bootstrap_draws=request.bootstrap_draws,
            permutation_draws=request.permutation_draws,
        )
        collusion["baseline_collusion_success_mean"] = collusion.pop("treatment_mean")
        collusion["intervened_collusion_success_mean"] = collusion.pop("control_mean")
        collusion["mean_baseline_minus_intervened"] = collusion.pop("mean_treatment_minus_control")
        collusion["median_family_baseline_minus_intervened"] = collusion.pop("median_family_effect")
        collusion["mediation_contribution_baseline_minus_intervened"] = collusion["mean_baseline_minus_intervened"]
        collusion["estimand"] = "equal-weight case-family mean baseline collusion success minus intervened success"
    utility = None
    if request.baseline_benign_utility is not None and request.intervened_benign_utility is not None:
        utility = clustered_paired_effect_summary(
            request.baseline_benign_utility,
            request.intervened_benign_utility,
            request.case_families,
            seed=request.seed + 23,
            bootstrap_draws=request.bootstrap_draws,
            permutation_draws=request.permutation_draws,
        )
        utility["baseline_benign_utility_mean"] = utility.pop("control_mean")
        utility["intervened_benign_utility_mean"] = utility.pop("treatment_mean")
        utility["utility_change_intervened_minus_baseline"] = utility.pop("mean_treatment_minus_control")
        utility["median_family_utility_change"] = utility.pop("median_family_effect")
        utility["utility_loss_baseline_minus_intervened"] = -float(utility["utility_change_intervened_minus_baseline"])
        utility["estimand"] = "equal-weight case-family mean intervened utility minus baseline utility"
    limitations = [
        "Secret decodability does not imply causal mediation; this report only summarizes supplied intervention outcomes.",
        "A valid matched intervention, sham/irrelevant-node control, and continued model execution are required for causal interpretation.",
        "The input arrays are caller supplied. Evidence mode and artifact references are metadata, not independent provenance verification.",
        "A drop in secret recovery is not specific if benign task utility or general generation ability also collapses.",
    ]
    if request.evidence_mode == "synthetic":
        limitations.append("Synthetic arrays validate software behavior only and cannot establish model mechanisms.")
    return AttributionNodeAnalysisReport(
        node_id=request.node_id,
        node_type=request.node_type,
        evidence_mode=request.evidence_mode,
        secret_recovery_mediation=recovery,
        collusion_success_mediation=collusion,
        benign_utility_change=utility,
        source_artifact_refs=request.source_artifact_refs,
        causal_claim_status="candidate_only",
        limitations=limitations,
    )


def _normalize(values: dict[str, float], keys: list[str]) -> list[float]:
    total = sum(values.get(key, 0.0) for key in keys)
    return [values.get(key, 0.0) / total for key in keys]


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for index, _value in ordered[i:j]:
            ranks[index] = rank
        i = j
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    a, b = _ranks(left), _ranks(right)
    ma, mb = mean(a), mean(b)
    da = [value - ma for value in a]
    db = [value - mb for value in b]
    denom = math.sqrt(sum(value * value for value in da) * sum(value * value for value in db))
    if denom == 0:
        return None
    return sum(x * y for x, y in zip(da, db)) / denom


def analyze_path_shift(request: PathShiftAnalysisRequest) -> PathShiftAnalysisReport:
    keys = sorted(set(request.contributions_before) | set(request.contributions_after))
    before = _normalize(request.contributions_before, keys)
    after = _normalize(request.contributions_after, keys)
    total_variation = 0.5 * sum(abs(a - b) for a, b in zip(before, after))
    dot = sum(a * b for a, b in zip(before, after))
    norm_before = math.sqrt(sum(value * value for value in before))
    norm_after = math.sqrt(sum(value * value for value in after))
    cosine_similarity = dot / (norm_before * norm_after) if norm_before and norm_after else 0.0
    before_top = [key for key, _ in sorted(zip(keys, before), key=lambda item: item[1], reverse=True)[:request.top_k]]
    after_top = [key for key, _ in sorted(zip(keys, after), key=lambda item: item[1], reverse=True)[:request.top_k]]
    union = set(before_top) | set(after_top)
    overlap = len(set(before_top) & set(after_top)) / len(union) if union else 1.0
    metrics = {
        "total_variation_distance": total_variation,
        "cosine_distance": 1.0 - cosine_similarity,
        "cosine_similarity": cosine_similarity,
        "top_k_jaccard_overlap": overlap,
        "top_k_before": before_top,
        "top_k_after": after_top,
        "node_count_union": len(keys),
        "normalized_contributions_before": dict(zip(keys, before)),
        "normalized_contributions_after": dict(zip(keys, after)),
        "spearman_rank_correlation": _spearman(before, after),
    }
    if request.evidence_mode == "synthetic":
        interpretation = "Synthetic contribution vectors show a software-level path-shift calculation only."
    elif total_variation >= 0.5:
        interpretation = "The supplied normalized contribution distribution changed substantially; validate protocol equivalence and measurement provenance."
    elif total_variation >= 0.2:
        interpretation = "The supplied normalized contribution distribution changed moderately; interpret with uncertainty and independent repeats."
    else:
        interpretation = "The supplied normalized contribution distribution changed little under this metric; this does not rule out unmeasured path migration."
    return PathShiftAnalysisReport(
        protocol_fingerprint=request.protocol_fingerprint_before,
        model_state_before=request.model_state_before,
        model_state_after=request.model_state_after,
        evidence_mode=request.evidence_mode,
        metrics=metrics,
        source_artifact_refs=request.source_artifact_refs,
        interpretation=interpretation,
        limitations=[
            "Contribution scores must be measured using the same attribution protocol, secret distribution, task, topology, and candidate definition before and after optimization.",
            "Distribution shift is descriptive and does not prove that an information path causally migrated.",
            "Path scores are caller supplied; source artifact references are not independently verified here.",
            "The top-k overlap depends on the selected k; report sensitivity to k in formal studies.",
        ] + (["Synthetic inputs cannot establish real model path migration."] if request.evidence_mode == "synthetic" else []),
    )
