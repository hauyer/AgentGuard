"""Matched control/treatment/negative-control execution for synthetic backends.

Real backends must inject a separately validated paired_executor that implements the
underlying runtime's true replay/perturbation contract. This module refuses to emulate
real counterfactual execution by changing only report labels.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.hypothesis import PairedExperimentDesign
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.paired import PairedExperimentExecutionReport, PairedExperimentPairResult
from agentguard.contracts.scenario import ScenarioSpec
from agentguard.core.statistics import paired_effect_summary


class PairedExecutionUnsupported(RuntimeError):
    pass


class PairedExperimentRunner:
    def __init__(
        self,
        runtime,
        paired_executor: Callable[..., PairedExperimentExecutionReport] | None = None,
        preregistration_verifier: Callable[[PairedExperimentDesign], bool] | None = None,
        output_artifact_verifier: Callable[[str], bool] | None = None,
    ):
        self.runtime = runtime
        self.paired_executor = paired_executor
        self.preregistration_verifier = preregistration_verifier
        self.output_artifact_verifier = output_artifact_verifier

    def run(
        self,
        *,
        run_id: str,
        scenario: ScenarioSpec,
        base_request: ExperimentRequest,
        design: PairedExperimentDesign,
        intervention_template: InterventionRequest,
        approval_granted: bool = False,
    ) -> PairedExperimentExecutionReport:
        capabilities: CapabilityManifest = self.runtime.capabilities()
        if base_request.policy_profile == "read_only":
            raise PermissionError("read_only policy profile forbids paired intervention experiments")
        if base_request.max_interventions < design.repetitions:
            raise ValueError("base request max_interventions is smaller than the paired design repetition count")
        if not intervention_template.authorization_ref:
            raise PermissionError("paired intervention template requires authorization_ref")
        if intervention_template.target_type not in scenario.allowed_targets:
            raise PermissionError("paired intervention target type is not allowed by the scenario")
        if intervention_template.target_type not in capabilities.supported_target_types or intervention_template.operation not in capabilities.supported_operations:
            raise PairedExecutionUnsupported("runtime capability manifest does not support this paired intervention target/operation")
        if capabilities.backend_mode == "real":
            if self.paired_executor is None:
                raise PairedExecutionUnsupported(
                    "Real backend paired execution requires an explicit, validated paired_executor; "
                    "run_file completion is not a counterfactual replay API."
                )
            if not approval_granted:
                raise PermissionError("real-backend paired execution requires explicit operator approval")
            if intervention_template.parameters.get("scope") != "isolated_test_environment":
                raise PermissionError("real-backend paired execution requires isolated_test_environment scope")
            if self.preregistration_verifier is None:
                raise PairedExecutionUnsupported(
                    "real paired execution requires a trusted pre-registration artifact verifier"
                )
            try:
                registration_verified = self.preregistration_verifier(design)
            except Exception as exc:
                raise PairedExecutionUnsupported(
                    "pre-registration verification failed: " + type(exc).__name__
                ) from exc
            if registration_verified is not True:
                raise PairedExecutionUnsupported("pre-registration artifact or immutable registration is unverified")
            if self.output_artifact_verifier is None:
                raise PairedExecutionUnsupported(
                    "real paired execution requires a trusted output-artifact verifier"
                )
            self._validate_real_design(
                scenario=scenario,
                base_request=base_request,
                design=design,
                intervention_template=intervention_template,
            )
            result = self.paired_executor(
                run_id=run_id, scenario=scenario, base_request=base_request,
                design=design, intervention_template=intervention_template,
            )
            if result.evidence_mode == "synthetic":
                raise ValueError("a real-backend paired_executor cannot label its result synthetic")
            if result.run_id != run_id or result.experiment_id != base_request.experiment_id or result.design_id != design.design_id:
                raise ValueError("paired_executor report identity does not match the requested run/design")
            self._validate_real_report(result, design, self.output_artifact_verifier)
            return result

        if intervention_template.parameters.get("scope") not in {"synthetic_demo", "isolated_test_environment"}:
            raise PermissionError("paired execution requires an explicitly scoped test environment")
        if base_request.policy_profile == "synthetic_demo" and capabilities.backend_mode != "synthetic":
            raise PermissionError("synthetic_demo policy profile cannot run on a real backend")
        pairs: list[PairedExperimentPairResult] = []
        control_by_metric: dict[str, list[float]] = {}
        treatment_by_metric: dict[str, list[float]] = {}
        control_for_negative: dict[str, list[float]] = {}
        negative_by_metric: dict[str, list[float]] = {}
        for seed in design.seeds:
            prefix = f"{base_request.experiment_id}-{design.design_id}-{seed}"
            control_id = f"{prefix}-control"
            treatment_id = f"{prefix}-treatment"
            negative_id = f"{prefix}-negative-control"
            pair = PairedExperimentPairResult(
                seed=seed, status="failed", control_experiment_id=control_id,
                treatment_experiment_id=treatment_id, negative_control_experiment_id=negative_id,
            )
            try:
                control_request = base_request.model_copy(update={
                    "experiment_id": control_id, "seed": seed, "auto_intervene": False,
                    "max_interventions": 0,
                })
                control_obs = self.runtime.execute(scenario, control_request)
                control_eval = self.runtime.evaluate(scenario, control_request, control_obs, None)
                pair.control_observation_count = len(control_obs)
                pair.control_metrics = self._metric_values(control_eval.post_intervention_metrics)

                treatment_request = base_request.model_copy(update={
                    "experiment_id": treatment_id, "seed": seed, "auto_intervene": False,
                    "max_interventions": 1,
                })
                treatment_obs = self.runtime.execute(scenario, treatment_request)
                pair.treatment_observation_count = len(treatment_obs)
                intervention_request = intervention_template.model_copy(update={
                    "experiment_id": treatment_id,
                    "intervention_id": f"paired-{design.design_id}-{seed}",
                    "control_group": f"matched-control-seed-{seed}",
                })
                pair.intervention_request = intervention_request
                try:
                    pair.intervention_result = self.runtime.intervene(intervention_request)
                except Exception as exc:  # noqa: BLE001 - capture backend failures without leaking messages
                    pair.intervention_result = InterventionResult(
                        intervention_id=intervention_request.intervention_id,
                        status="runtime_error", changed=False, effect_status="not_measured",
                        message="Runtime raised during paired intervention; raw exception text is not copied into the report.",
                        error_code=type(exc).__name__,
                    )
                    pair.error_code = type(exc).__name__
                treatment_eval = self.runtime.evaluate(scenario, treatment_request, treatment_obs, pair.intervention_result)
                pair.treatment_metrics = self._metric_values(treatment_eval.post_intervention_metrics)

                negative_request = base_request.model_copy(update={
                    "experiment_id": negative_id, "seed": seed, "auto_intervene": False,
                    "max_interventions": 0,
                })
                negative_obs = self.runtime.execute(scenario, negative_request)
                negative_eval = self.runtime.evaluate(scenario, negative_request, negative_obs, None)
                pair.negative_control_observation_count = len(negative_obs)
                pair.negative_control_metrics = self._metric_values(negative_eval.post_intervention_metrics)

                if pair.intervention_result.status == "completed" and pair.control_metrics and pair.treatment_metrics:
                    pair.status = "completed"
                else:
                    pair.status = "partial"
                    pair.notes.append("Treatment action did not complete; do not interpret this pair as a successful defense.")
                if pair.status == "completed":
                    for name in set(pair.control_metrics) & set(pair.treatment_metrics):
                        control_by_metric.setdefault(name, []).append(pair.control_metrics[name])
                        treatment_by_metric.setdefault(name, []).append(pair.treatment_metrics[name])
                for name in set(pair.control_metrics) & set(pair.negative_control_metrics):
                    control_for_negative.setdefault(name, []).append(pair.control_metrics[name])
                    negative_by_metric.setdefault(name, []).append(pair.negative_control_metrics[name])
            except Exception as exc:  # noqa: BLE001 - persist only a bounded failure code
                pair.status = "failed"
                pair.error_code = type(exc).__name__
                pair.notes.append("Pair execution failed; raw backend error text is intentionally not copied into the report.")
            pairs.append(pair)

        completed = sum(pair.status == "completed" for pair in pairs)
        aggregate: dict[str, Any] = {}
        negative_stats: dict[str, Any] = {}
        for name in sorted(set(control_by_metric) & set(treatment_by_metric)):
            controls = control_by_metric[name]
            treatments = treatment_by_metric[name]
            if len(controls) >= 2:
                aggregate[name] = paired_effect_summary(controls, treatments, seed=base_request.seed)
        for name in sorted(set(control_for_negative) & set(negative_by_metric)):
            controls = control_for_negative[name]
            negatives = negative_by_metric[name]
            if len(controls) >= 2:
                negative_stats[name] = paired_effect_summary(controls, negatives, seed=base_request.seed + 101)
        if completed == len(pairs) and len(pairs) >= 2:
            status = "completed"
        elif completed or any(pair.status == "partial" for pair in pairs):
            status = "partial"
        else:
            status = "failed"
        return PairedExperimentExecutionReport(
            run_id=run_id,
            experiment_id=base_request.experiment_id,
            design_id=design.design_id,
            hypothesis_id=design.hypothesis_id,
            status=status,
            evidence_mode="synthetic",
            causal_claim_status="synthetic_only",
            requested_pairs=len(design.seeds),
            completed_pairs=completed,
            pairs=pairs,
            aggregate_statistics=aggregate,
            negative_control_statistics=negative_stats,
            limitations=[
                "This built-in runner is available only for synthetic backends; it does not emulate real counterfactual execution.",
                "The Mock evaluator scripts intervention effects to validate the workflow, so its effect sizes are not empirical.",
                "The legacy negative-control field is only a same-seed no-intervention replay; it is not the required sham or unrelated-position negative control.",
                "Sham and unrelated-position controls remain not_run in this synthetic runner and block any causal claim.",
                "Statistical summaries do not establish causal validity without valid matching, provenance, preregistration and independent review.",
            ],
        )

    @staticmethod
    def _metric_values(metrics) -> dict[str, float]:
        return {name: float(metric.value) for name, metric in metrics.items() if isinstance(metric.value, (int, float))}

    @staticmethod
    def _validate_real_design(*, scenario, base_request, design, intervention_template) -> None:
        if not design.preregistered:
            raise PairedExecutionUnsupported("real paired execution requires a complete pre-registered design")
        if design.scenario_id != scenario.scenario_id:
            raise ValueError("pre-registered scenario does not match the requested scenario")
        if not base_request.model_version or design.model_revision != base_request.model_version:
            raise ValueError("pre-registered model revision must match the requested model revision")
        if design.preregistration_artifact_ref != f"artifact://sha256/{design.preregistration_sha256}":
            raise ValueError("pre-registration artifact reference must match its declared SHA-256")
        if intervention_template.operation != design.treatment_operation:
            raise ValueError("intervention operation differs from the pre-registered treatment")
        if (
            intervention_template.target_type != design.candidate_target_type
            or intervention_template.target_ref != design.candidate_target_ref
        ):
            raise ValueError("intervention target differs from the pre-registered candidate target")

    @staticmethod
    def _validate_real_report(result, design, output_artifact_verifier) -> None:
        expected = {
            "design_fingerprint": design.design_fingerprint,
            "preregistration_artifact_ref": design.preregistration_artifact_ref,
            "preregistration_sha256": design.preregistration_sha256,
            "model_revision": design.model_revision,
            "decoding_settings_sha256": design.decoding_settings_sha256,
            "control_condition": design.control_condition,
            "treatment_condition": design.treatment_condition,
            "control_operation": design.control_operation,
            "treatment_operation": design.treatment_operation,
            "sham_control_operation": design.sham_control_operation,
            "unrelated_position_control_operation": design.negative_control_operation,
            "randomization_or_permutation_baseline": design.randomization_or_permutation_baseline,
            "hidden_truth_excluded_from_model_input": True,
            "independent_case_family_ids": design.independent_case_family_ids,
        }
        mismatches = [name for name, value in expected.items() if getattr(result, name) != value]
        if mismatches:
            raise ValueError("paired_executor report does not match the pre-registered design: " + ", ".join(mismatches))
        if result.causal_claim_status != "causal_validation_required":
            raise ValueError("paired execution cannot claim causal validation before trusted artifact review")
        if result.evidence_mode == "synthetic":
            raise ValueError("a real-backend paired_executor cannot return synthetic evidence")
        if result.requested_pairs != design.repetitions or len(result.pairs) != design.repetitions:
            raise ValueError("paired_executor must preserve every pre-registered repetition, including failures")
        if len({pair.seed for pair in result.pairs}) != len(result.pairs) or {pair.seed for pair in result.pairs} != set(design.seeds):
            raise ValueError("paired_executor report seeds do not match the pre-registered schedule")
        actual_completed = sum(pair.status == "completed" for pair in result.pairs)
        if result.completed_pairs != actual_completed:
            raise ValueError("completed_pairs does not match the retained pair records")
        if result.status == "completed" and actual_completed != design.repetitions:
            raise ValueError("a completed paired report must retain every successful repetition")
        report_artifacts = (
            result.execution_log_artifact_ref,
            result.intervention_parameters_artifact_ref,
            result.statistical_report_artifact_ref,
        )
        if result.status == "completed":
            if not all(report_artifacts) or not result.evaluator_version:
                raise ValueError("completed real reports require execution, intervention, evaluator and statistical artifacts")
            if not PairedExperimentRunner._hash_ref_matches(
                result.execution_log_artifact_ref, result.execution_log_sha256,
            ):
                raise ValueError("execution-log reference does not match its declared SHA-256")
            if not PairedExperimentRunner._hash_ref_matches(
                result.intervention_parameters_artifact_ref, result.intervention_parameters_sha256,
            ):
                raise ValueError("intervention-parameter reference does not match its declared SHA-256")
        artifacts_to_verify = [ref for ref in report_artifacts if ref]
        for pair in result.pairs:
            if pair.status != "completed":
                continue
            if (
                pair.sham_control_status != "completed"
                or pair.unrelated_position_control_status != "completed"
                or pair.randomization_baseline_status != "completed"
                or pair.sham_control_observation_count < 1
                or pair.unrelated_position_control_observation_count < 1
                or pair.randomization_baseline_observation_count < 1
                or pair.control_observation_count < 1
                or pair.treatment_observation_count < 1
            ):
                raise ValueError("completed real pairs require baseline, sham, unrelated-position and randomization controls")
            if not all((
                pair.sham_control_experiment_id,
                pair.unrelated_position_control_experiment_id,
                pair.randomization_baseline_experiment_id,
                pair.control_output_artifact_ref,
                pair.treatment_output_artifact_ref,
                pair.sham_control_output_artifact_ref,
                pair.unrelated_position_control_output_artifact_ref,
                pair.randomization_baseline_output_artifact_ref,
                pair.intervention_result.before_artifact_ref if pair.intervention_result else None,
                pair.intervention_result.after_artifact_ref if pair.intervention_result else None,
            )):
                raise ValueError("completed real pairs require stable IDs and output artifact references for every arm")
            if pair.intervention_result is None or pair.intervention_result.status != "completed":
                raise ValueError("completed real pairs require a completed target intervention record")
            required_metrics = {
                design.primary_metric, design.utility_metric, design.secret_recovery_metric,
            }
            for arm_metrics in (
                pair.control_metrics, pair.treatment_metrics, pair.sham_control_metrics,
                pair.unrelated_position_control_metrics, pair.randomization_baseline_metrics,
            ):
                if not required_metrics.issubset(arm_metrics):
                    raise ValueError("completed real pairs require primary, utility and secret-recovery metrics for every arm")
            artifacts_to_verify.extend((
                pair.control_output_artifact_ref,
                pair.treatment_output_artifact_ref,
                pair.sham_control_output_artifact_ref,
                pair.unrelated_position_control_output_artifact_ref,
                pair.randomization_baseline_output_artifact_ref,
                pair.intervention_result.before_artifact_ref,
                pair.intervention_result.after_artifact_ref,
            ))
        for ref in dict.fromkeys(artifacts_to_verify):
            if not PairedExperimentRunner._content_addressed_ref(ref):
                raise ValueError("real paired outputs must use content-addressed artifact references")
            try:
                verified = output_artifact_verifier(ref)
            except Exception as exc:
                raise ValueError("output-artifact verification failed: " + type(exc).__name__) from exc
            if verified is not True:
                raise ValueError("real paired output artifact failed trusted content/ownership verification")

    @staticmethod
    def _content_addressed_ref(ref: str | None) -> bool:
        if not isinstance(ref, str):
            return False
        return ref.startswith("artifact://sha256/") and len(ref) == len("artifact://sha256/") + 64 and all(
            character in "0123456789abcdef" for character in ref.removeprefix("artifact://sha256/")
        )

    @staticmethod
    def _hash_ref_matches(ref: str | None, digest: str | None) -> bool:
        return isinstance(digest, str) and PairedExperimentRunner._content_addressed_ref(ref) and ref == f"artifact://sha256/{digest}"
