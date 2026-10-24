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
    def __init__(self, runtime, paired_executor: Callable[..., PairedExperimentExecutionReport] | None = None):
        self.runtime = runtime
        self.paired_executor = paired_executor

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
            result = self.paired_executor(
                run_id=run_id, scenario=scenario, base_request=base_request,
                design=design, intervention_template=intervention_template,
            )
            if result.evidence_mode == "synthetic":
                raise ValueError("a real-backend paired_executor cannot label its result synthetic")
            if result.run_id != run_id or result.experiment_id != base_request.experiment_id or result.design_id != design.design_id:
                raise ValueError("paired_executor report identity does not match the requested run/design")
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
                "The negative control is a same-seed no-intervention replay; it checks replay stability, not every confounder.",
                "Statistical summaries do not establish causal validity without valid matching, provenance, preregistration and independent review.",
            ],
        )

    @staticmethod
    def _metric_values(metrics) -> dict[str, float]:
        return {name: float(metric.value) for name, metric in metrics.items() if isinstance(metric.value, (int, float))}
