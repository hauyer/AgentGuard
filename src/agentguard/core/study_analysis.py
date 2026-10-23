"""Preregistered, family-aware analysis of paired outcome arrays."""
from __future__ import annotations

from agentguard.contracts.research import (
    RegisteredStudy,
    StudyAnalysisReport,
    StudyAnalysisRequest,
)
from agentguard.core.statistics import (
    adjust_pvalues,
    clustered_paired_effect_summary,
    paired_effect_summary,
)
from agentguard.research_registry import sha256_json


def analyze_registered_study(
    study: RegisteredStudy,
    request: StudyAnalysisRequest,
) -> StudyAnalysisReport:
    allowed = {study.primary_outcome, *study.secondary_outcomes}
    submitted = set(request.outcomes)
    if study.primary_outcome not in submitted:
        raise ValueError("the preregistered primary outcome must be included")
    unknown = submitted - allowed
    if unknown:
        raise ValueError(f"unregistered outcomes cannot be analyzed confirmatorily: {sorted(unknown)}")
    missing_registered = allowed - submitted
    if missing_registered:
        raise ValueError(
            "all registered secondary outcomes must be included in the confirmatory analysis "
            f"to preserve the planned multiplicity family: {sorted(missing_registered)}"
        )
    observed_data_sha256 = sha256_json({
        name: data.model_dump(mode="json") for name, data in request.outcomes.items()
    })
    if request.data_sha256 is not None and request.data_sha256 != observed_data_sha256:
        raise ValueError("data_sha256 does not match the canonical submitted outcomes payload")

    outcome_results: dict[str, dict] = {}
    raw_p_values: dict[str, float] = {}
    deviations: list[str] = []
    for name, data in request.outcomes.items():
        n_pairs = len(data.control)
        n_families = len(set(data.case_families))
        if study.analysis_method == "clustered_paired":
            summary = clustered_paired_effect_summary(
                data.control,
                data.treatment,
                data.case_families,
                seed=study.seed,
                bootstrap_draws=request.bootstrap_draws,
                permutation_draws=request.permutation_draws,
            )
            p_value = float(summary["family_level_sign_flip_pvalue_two_sided"])
        else:
            summary = paired_effect_summary(
                data.control,
                data.treatment,
                seed=study.seed,
                bootstrap_draws=request.bootstrap_draws,
                permutation_draws=request.permutation_draws,
            )
            p_value = float(summary["paired_permutation_pvalue_two_sided"])
        raw_p_values[name] = p_value
        outcome_results[name] = {
            "summary": summary,
            "raw_p_value": p_value,
            "adjusted_p_value": None,
            "reject_null": False,
            "registered_role": "primary" if name == study.primary_outcome else "secondary",
        }
        if n_pairs < study.planned_pairs:
            deviations.append(f"Outcome {name}: observed {n_pairs} pairs, below preregistered target {study.planned_pairs}.")
        if n_families < study.planned_case_families:
            deviations.append(f"Outcome {name}: observed {n_families} case families, below preregistered target {study.planned_case_families}.")

    adjusted = adjust_pvalues(raw_p_values, method=study.multiplicity_method)
    for name, correction in adjusted.items():
        result = outcome_results[name]
        result["adjusted_p_value"] = correction["adjusted_p_value"]
        effect = float(result["summary"]["mean_treatment_minus_control"])
        significant = float(correction["adjusted_p_value"]) <= study.alpha
        if name == study.primary_outcome:
            if study.primary_direction == "increase":
                direction_ok = effect > 0
            elif study.primary_direction == "decrease":
                direction_ok = effect < 0
            else:
                direction_ok = True
        else:
            direction_ok = True
        result["reject_null"] = bool(significant and direction_ok)
        result["direction_consistent"] = direction_ok

    primary = outcome_results[study.primary_outcome]
    limitations = [
        "This endpoint analysis uses caller-supplied paired arrays; artifact references and hashes are metadata, not independent proof of provenance.",
        "Preregistration constrains endpoint selection but does not prove randomization, matching validity, or causal identification.",
        "The confirmatory decision uses the registered primary direction, alpha, analysis method, and multiplicity procedure.",
    ]
    if request.evidence_mode == "synthetic":
        limitations.append("Synthetic evidence cannot establish real-model or deployment effectiveness.")
    if study.multiplicity_method == "none" and len(outcome_results) > 1:
        deviations.append("Multiple outcomes were analyzed without multiplicity correction, as preregistered.")
    payload_for_hash = {
        "study_id": study.study_id,
        "registration_sha256": study.registration_sha256,
        "request": request.model_dump(mode="json"),
        "data_sha256": observed_data_sha256,
        "outcomes": outcome_results,
    }
    return StudyAnalysisReport(
        study_id=study.study_id,
        registration_sha256=study.registration_sha256,
        analysis_sha256=sha256_json(payload_for_hash),
        evidence_mode=request.evidence_mode,
        primary_outcome=study.primary_outcome,
        outcomes=outcome_results,
        multiplicity_method=study.multiplicity_method,
        alpha=study.alpha,
        confirmatory_primary_result=bool(primary["reject_null"]),
        source_artifact_refs=request.source_artifact_refs,
        data_sha256=observed_data_sha256,
        deviations=deviations,
        limitations=limitations,
    )
