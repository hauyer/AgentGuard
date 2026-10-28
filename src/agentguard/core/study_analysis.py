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
    spearman_family_bootstrap,
)
from agentguard.research_registry import sha256_json


def analyze_registered_study(
    study: RegisteredStudy,
    request: StudyAnalysisRequest,
) -> StudyAnalysisReport:
    if request.evidence_mode == "measured":
        raise ValueError("ARTIFACT_CONTENT_NOT_VERIFIED: measured outcomes require a verified WARDEN artifact import")
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
    planned_arms = {arm.arm_id: arm for arm in study.arms if arm.role != "baseline"}
    if request.arm_analyses and not study.arms:
        raise ValueError("arm analyses require arms frozen in the preregistered study")
    unknown_arms = set(request.arm_analyses) - set(planned_arms)
    if unknown_arms:
        raise ValueError(f"analysis references unregistered arms: {sorted(unknown_arms)}")
    association = request.probe_effect_association
    if association is not None:
        if study.model_revision is None or association.model_revision != study.model_revision:
            raise ValueError("Probe association model_revision does not match the preregistered study")
        if study.label_semantics is None or association.label_semantics != study.label_semantics:
            raise ValueError("Probe association label semantics do not match the preregistered study")
        frozen_coordinates = (
            study.probe_scope, study.probe_target, study.probe_layer, study.probe_token,
        )
        submitted_coordinates = (
            association.probe_scope, association.target, association.layer, association.token,
        )
        if not all(frozen_coordinates) or frozen_coordinates != submitted_coordinates:
            raise ValueError("Probe association coordinates do not match the preregistered study")

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
            "metric_type": study.metric_definitions.get(name),
            "unit": study.metric_units.get(name),
            "analysis_population": (
                "intent_to_treat" if study.metric_definitions.get(name) == "SRR_ITT" else
                "execution_conditional" if study.metric_definitions.get(name) == "SRR_executed" else
                "registered_paired_input"
            ),
        }
        if n_pairs < study.planned_pairs:
            deviations.append(f"Outcome {name}: observed {n_pairs} pairs, below preregistered target {study.planned_pairs}.")
        if n_families < study.planned_case_families:
            deviations.append(f"Outcome {name}: observed {n_families} case families, below preregistered target {study.planned_case_families}.")

    arm_results: dict[str, dict] = {}
    arm_raw_p_values: dict[str, str] = {}
    reference_families = tuple(next(iter(request.outcomes.values())).case_families)

    def execution_counts(arm_input, *, observed_completed_pairs: int | None = None) -> dict:
        explicit = arm_input is not None and arm_input.planned_pairs is not None
        if explicit and arm_input.planned_pairs != study.planned_pairs:
            raise ValueError("arm execution counts must use the preregistered planned_pairs denominator")
        completed = (
            arm_input.completed_pairs if explicit
            else observed_completed_pairs
        ) if arm_input is not None else observed_completed_pairs
        failed = arm_input.failed_pairs if explicit else None
        blocked = arm_input.blocked_pairs if explicit else None
        not_run = arm_input.not_run_pairs if explicit else None
        return {
            "planned_pairs": study.planned_pairs,
            "completed_pairs": completed,
            "failed_pairs": failed,
            "blocked_pairs": blocked,
            "not_run_pairs": not_run,
            "failure_rate": failed / study.planned_pairs if explicit else None,
            "noncompleted_rate": (
                (failed + blocked + not_run) / study.planned_pairs if explicit else None
            ),
            "execution_rate": completed / study.planned_pairs if explicit else None,
            "count_completeness": "complete" if explicit else "incomplete",
            "source_validation": "unverified",
        }

    for arm_id in sorted(planned_arms):
        arm_input = request.arm_analyses.get(arm_id)
        if arm_input is None:
            reason = "ARM_RESULT_NOT_PROVIDED"
            arm_results[arm_id] = {
                "status": "unknown",
                "unknown_reason": reason,
                "execution_counts": execution_counts(None),
                "outcomes": {name: {"effect": None, "unknown_reason": reason} for name in sorted(allowed)},
            }
            deviations.append(f"Arm {arm_id}: no execution result was provided; the arm remains unknown.")
            continue
        if arm_input.status != "completed":
            reason = arm_input.failure_reason or arm_input.unknown_reason or f"ARM_STATUS_{arm_input.status.upper()}"
            arm_results[arm_id] = {
                "status": arm_input.status,
                "failure_reason": arm_input.failure_reason,
                "unknown_reason": reason,
                "execution_counts": execution_counts(arm_input),
                "received_outcomes": sorted(arm_input.outcomes),
                "outcomes": {name: {"effect": None, "unknown_reason": reason} for name in sorted(allowed)},
            }
            deviations.append(f"Arm {arm_id}: status={arm_input.status}; no success result was inferred.")
            continue
        if set(arm_input.outcomes) != allowed:
            raise ValueError(f"completed arm {arm_id} must include every preregistered outcome")
        families = tuple(next(iter(arm_input.outcomes.values())).case_families)
        observed_completed_pairs = len(next(iter(arm_input.outcomes.values())).control)
        if arm_input.planned_pairs is not None and arm_input.completed_pairs != observed_completed_pairs:
            raise ValueError(f"arm {arm_id} completed_pairs does not match supplied outcome arrays")
        if families != reference_families:
            reason = "CASE_FAMILY_MISMATCH"
            arm_results[arm_id] = {
                "status": "not_comparable",
                "unknown_reason": reason,
                "execution_counts": execution_counts(
                    arm_input, observed_completed_pairs=observed_completed_pairs,
                ),
                "outcomes": {name: {"effect": None, "unknown_reason": reason} for name in sorted(allowed)},
            }
            deviations.append(f"Arm {arm_id}: family IDs/order do not match the registered comparison; results were not compared.")
            continue
        summarized: dict[str, dict] = {}
        for name, data in arm_input.outcomes.items():
            if tuple(data.case_families) != families:
                raise ValueError(f"arm {arm_id} outcomes use different case families")
            if study.analysis_method == "clustered_paired":
                summary = clustered_paired_effect_summary(
                    data.control, data.treatment, data.case_families,
                    seed=study.seed, bootstrap_draws=request.bootstrap_draws,
                    permutation_draws=request.permutation_draws,
                )
                p_value = float(summary["family_level_sign_flip_pvalue_two_sided"])
            else:
                summary = paired_effect_summary(
                    data.control, data.treatment, seed=study.seed,
                    bootstrap_draws=request.bootstrap_draws,
                    permutation_draws=request.permutation_draws,
                )
                p_value = float(summary["paired_permutation_pvalue_two_sided"])
            key = f"{arm_id}::{name}"
            raw_p_values[key] = p_value
            arm_raw_p_values[key] = name
            summarized[name] = {
                "summary": summary,
                "raw_p_value": p_value,
                "adjusted_p_value": None,
                "registered_role": "primary" if name == study.primary_outcome else "secondary",
                "metric_type": study.metric_definitions.get(name),
                "unit": study.metric_units.get(name),
            }
            if len(set(data.case_families)) < study.planned_case_families:
                deviations.append(
                    f"Arm {arm_id}, outcome {name}: fewer case families than preregistered; the estimate is under-plan."
                )
        arm_results[arm_id] = {
            "status": "completed",
            "execution_counts": execution_counts(
                arm_input, observed_completed_pairs=observed_completed_pairs,
            ),
            "outcomes": summarized,
        }

    adjusted = adjust_pvalues(raw_p_values, method=study.multiplicity_method)
    for name, result in outcome_results.items():
        correction = adjusted[name]
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

    for arm_id, result in arm_results.items():
        for outcome, item in result.get("outcomes", {}).items():
            if "summary" not in item:
                continue
            correction = adjusted[f"{arm_id}::{outcome}"]
            item["adjusted_p_value"] = correction["adjusted_p_value"]
            item["statistically_significant"] = float(correction["adjusted_p_value"]) <= study.alpha

    primary = outcome_results[study.primary_outcome]
    limitations = [
        "This endpoint analysis uses caller-supplied paired arrays; artifact references and hashes are metadata, not independent proof of provenance.",
        "Preregistration constrains endpoint selection but does not prove randomization, matching validity, or causal identification.",
        "The confirmatory decision uses the registered primary direction, alpha, analysis method, and multiplicity procedure.",
    ]
    if request.evidence_mode == "synthetic":
        limitations.append("Synthetic evidence cannot establish real-model or deployment effectiveness.")
    if any(
        item.get("execution_counts", {}).get("count_completeness") != "complete"
        for item in arm_results.values()
    ):
        limitations.append("Full ITT failure/blocked/not-run rates remain unavailable for arms without complete preregistered pair counts.")
    if any(metric in {"LATENCY", "GPU_COST", "FALSE_POSITIVE_COST"} for metric in study.metric_definitions.values()):
        limitations.append("RQ3 resource metrics are paired caller inputs and remain unverified until imported from hash-checked WARDEN artifacts.")
    else:
        limitations.append("RQ3 latency/GPU/failure costs were not preregistered for this Study.")
    if study.multiplicity_method == "none" and len(outcome_results) > 1:
        deviations.append("Multiple outcomes were analyzed without multiplicity correction, as preregistered.")
    if study.metric_definitions:
        for outcome, metric in study.metric_definitions.items():
            if metric == "SRR_ITT":
                for arm_id, arm_result in arm_results.items():
                    if arm_result.get("status") != "completed":
                        continue
                    data = request.arm_analyses[arm_id].outcomes[outcome]
                    if len(set(data.case_families)) < study.planned_case_families:
                        deviations.append(
                            f"RQ1 SRR_ITT for {arm_id} is incomplete against all planned families."
                        )

    rq2_result = None
    if association is not None:
        rq2_result = spearman_family_bootstrap(
            association.probe_scores, association.intervention_effects,
            association.case_families, seed=study.seed,
            bootstrap_draws=request.bootstrap_draws,
        )
        rq2_result.update({
            "probe_scope": association.probe_scope,
            "target": association.target,
            "layer": association.layer,
            "token": association.token,
            "model_revision": association.model_revision,
            "label_semantics": association.label_semantics,
            "split": association.split,
            "source_validation": (
                "synthetic_only" if request.evidence_mode == "synthetic" else
                "script_replay" if request.evidence_mode == "script_replay" else "unverified"
            ),
        })

    rq3_rows: list[dict] = []
    safety_outcome = next((name for name, metric in study.metric_definitions.items() if metric == "SRR_ITT"), None)
    utility_outcome = next((name for name, metric in study.metric_definitions.items() if metric == "NORMAL_UTILITY"), None)
    resource_outcomes = {
        name: metric for name, metric in study.metric_definitions.items()
        if metric in {"LATENCY", "GPU_COST", "FALSE_POSITIVE_COST"}
    }
    if safety_outcome and utility_outcome:
        for arm_id in sorted(planned_arms):
            result = arm_results[arm_id]
            safety = result.get("outcomes", {}).get(safety_outcome, {})
            utility = result.get("outcomes", {}).get(utility_outcome, {})
            safety_summary = safety.get("summary")
            utility_summary = utility.get("summary")
            execution = result.get("execution_counts")
            if result.get("status") != "completed" or not isinstance(safety_summary, dict) or not isinstance(utility_summary, dict):
                rq3_rows.append({
                    "arm_id": arm_id,
                    "status": result.get("status", "unknown"),
                    "safety_effect": None,
                    "utility_loss": None,
                    "execution": execution,
                    "resource_metrics": {
                        name: {"metric_type": metric, "value": None, "unit": study.metric_units.get(name),
                               "unknown_reason": result.get("unknown_reason", "RQ3_METRIC_UNAVAILABLE")}
                        for name, metric in resource_outcomes.items()
                    },
                    "unknown_reason": result.get("unknown_reason", "RQ3_METRIC_UNAVAILABLE"),
                })
                continue
            resource_metrics: dict[str, dict] = {}
            for name, metric in resource_outcomes.items():
                summary = result.get("outcomes", {}).get(name, {}).get("summary")
                resource_metrics[name] = (
                    {
                        "metric_type": metric,
                        "unit": study.metric_units.get(name),
                        "mean_treatment_minus_control": summary["mean_treatment_minus_control"],
                        "control_mean": summary["control_mean"],
                        "treatment_mean": summary["treatment_mean"],
                        "n_pairs": summary["n_pairs"],
                    }
                    if isinstance(summary, dict) else
                    {"metric_type": metric, "unit": study.metric_units.get(name),
                     "value": None, "unknown_reason": "RQ3_METRIC_UNAVAILABLE"}
                )
            rq3_rows.append({
                "arm_id": arm_id,
                "status": "estimated",
                "safety_effect": -float(safety_summary["mean_treatment_minus_control"]),
                "utility_loss": -float(utility_summary["mean_treatment_minus_control"]),
                "safety_metric": safety_outcome,
                "safety_unit": study.metric_units.get(safety_outcome),
                "utility_metric": utility_outcome,
                "utility_unit": study.metric_units.get(utility_outcome),
                "evidence_mode": request.evidence_mode,
                "source_validation": "synthetic_only" if request.evidence_mode == "synthetic" else
                                     "script_replay" if request.evidence_mode == "script_replay" else "unverified",
                "execution": execution,
                "resource_metrics": resource_metrics,
            })
        measured_rows = [row for row in rq3_rows if row.get("status") == "estimated"]
        for row in measured_rows:
            row["pareto_frontier"] = not any(
                other["arm_id"] != row["arm_id"]
                and float(other["safety_effect"]) >= float(row["safety_effect"])
                and float(other["utility_loss"]) <= float(row["utility_loss"])
                and (
                    float(other["safety_effect"]) > float(row["safety_effect"])
                    or float(other["utility_loss"]) < float(row["utility_loss"])
                )
                for other in measured_rows
            )
        for row in rq3_rows:
            row.setdefault("pareto_frontier", None)
    payload_for_hash = {
        "study_id": study.study_id,
        "registration_sha256": study.registration_sha256,
        "request": request.model_dump(mode="json"),
        "data_sha256": observed_data_sha256,
        "outcomes": outcome_results,
        "arm_analyses": arm_results,
        "rq2_probe_effect_association": rq2_result,
        "rq3_safety_utility": rq3_rows,
    }
    return StudyAnalysisReport(
        study_id=study.study_id,
        registration_sha256=study.registration_sha256,
        analysis_sha256=sha256_json(payload_for_hash),
        evidence_mode=request.evidence_mode,
        primary_outcome=study.primary_outcome,
        planned_pairs=study.planned_pairs,
        planned_case_families=study.planned_case_families,
        outcomes=outcome_results,
        multiplicity_method=study.multiplicity_method,
        alpha=study.alpha,
        confirmatory_primary_result=bool(primary["reject_null"]),
        source_artifact_refs=request.source_artifact_refs,
        data_sha256=observed_data_sha256,
        deviations=deviations,
        limitations=limitations,
        arm_analyses=arm_results,
        rq2_probe_effect_association=rq2_result,
        rq3_safety_utility=rq3_rows,
        causal_claim_status="synthetic_only" if request.evidence_mode == "synthetic" else "not_assessed",
        source_validation=(
            "synthetic_only" if request.evidence_mode == "synthetic" else
            "script_replay" if request.evidence_mode == "script_replay" else "unverified"
        ),
    )
