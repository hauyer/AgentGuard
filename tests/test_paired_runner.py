import pytest

from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.hypothesis import PairedExperimentDesign
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.paired import PairedExperimentExecutionReport, PairedExperimentPairResult
from agentguard.core.orchestrator import AgentGuardEngine
from agentguard.core.paired_runner import PairedExecutionUnsupported, PairedExperimentRunner


def _prepared_run(max_interventions=5):
    backend = MockBackend()
    request = ExperimentRequest(
        scenario_id="demo_cloud_security", runtime_backend="mock",
        auto_intervene=False, max_interventions=max_interventions, seed=31,
    )
    report = AgentGuardEngine(backend, backend, backend).run(request)
    design = report.experiment_designs[0]
    action = next(item for item in report.investigation_actions if item.hypothesis_id == design.hypothesis_id)
    scenario = backend.get_scenario(report.scenario_id)
    template = InterventionRequest(
        experiment_id=request.experiment_id, intervention_id="paired-template",
        target_type=action.target_type, target_ref=action.target_ref, operation=action.operation,
        parameters={"scope": "synthetic_demo"}, authorization_ref="test-approval-ref",
    )
    return backend, request, report, scenario, design, template


def test_paired_runner_executes_matched_control_treatment_and_negative_control():
    backend, request, report, scenario, design, template = _prepared_run()
    execution = PairedExperimentRunner(backend).run(
        run_id=report.run_id, scenario=scenario, base_request=request,
        design=design, intervention_template=template,
    )
    assert execution.status == "completed"
    assert execution.evidence_mode == "synthetic"
    assert execution.causal_claim_status == "synthetic_only"
    assert execution.completed_pairs == design.repetitions == 5
    assert execution.aggregate_statistics["risk_exposure"]["mean_treatment_minus_control"] < 0
    assert execution.negative_control_statistics["risk_exposure"]["mean_treatment_minus_control"] == 0
    assert all(pair.control_observation_count == pair.treatment_observation_count == pair.negative_control_observation_count for pair in execution.pairs)


def test_paired_runner_refuses_to_fake_real_counterfactual_execution():
    backend, request, report, scenario, design, template = _prepared_run()
    class RealModeWrapper:
        backend_mode = "real"
        def capabilities(self):
            return backend.capabilities().model_copy(update={"backend_mode": "real"})
    with pytest.raises(PairedExecutionUnsupported):
        PairedExperimentRunner(RealModeWrapper()).run(
            run_id=report.run_id, scenario=scenario, base_request=request,
            design=design, intervention_template=template,
        )


def _preregistered_design(design, request, scenario, template):
    payload = design.model_dump(mode="python")
    payload.update({
        "preregistered": True,
        "scenario_id": scenario.scenario_id,
        "model_revision": "model@revision-1",
        "decoding_settings": {"temperature": 0.0, "top_p": 1.0},
        "control_condition": "secret_off",
        "treatment_condition": "secret_on",
        "candidate_target_type": template.target_type,
        "candidate_target_ref": template.target_ref,
        "sham_control_operation": "sham_intervention",
        "negative_control_operation": "unrelated_position_intervention",
        "utility_metric": "benign_task_utility",
        "secret_recovery_metric": "secret_recovery_rate",
        "stopping_condition": "stop_at_declared_budget_or_registered_failure",
        "exclusion_rules": ["exclude only infrastructure failures declared before execution"],
        "randomization_or_permutation_baseline": "seeded sign-flip permutation test",
        "hidden_truth_excluded_from_model_input": True,
        "independent_case_family_ids": ["family-a", "family-b"],
        "preregistration_artifact_ref": f"artifact://sha256/{'a' * 64}",
        "preregistration_sha256": "a" * 64,
        "matched_on": [
            "scenario_id", "case_family", "seed", "model_revision", "task_input",
            "decoding_settings",
        ],
    })
    return PairedExperimentDesign.model_validate(payload)


def _real_executor_report(run_id, request, design):
    metric_values = {
        "risk_exposure": 0.2,
        "benign_task_utility": 0.95,
        "secret_recovery_rate": 0.1,
    }
    pairs = []
    for seed in design.seeds:
        prefix = f"{request.experiment_id}-{design.design_id}-{seed}"
        pairs.append(PairedExperimentPairResult(
            seed=seed,
            status="completed",
            control_experiment_id=f"{prefix}-control",
            treatment_experiment_id=f"{prefix}-treatment",
            negative_control_experiment_id=f"{prefix}-replay",
            sham_control_experiment_id=f"{prefix}-sham",
            unrelated_position_control_experiment_id=f"{prefix}-unrelated",
            sham_control_status="completed",
            unrelated_position_control_status="completed",
            randomization_baseline_status="completed",
            control_observation_count=1,
            treatment_observation_count=1,
            sham_control_observation_count=1,
            unrelated_position_control_observation_count=1,
            randomization_baseline_observation_count=1,
            control_metrics=metric_values,
            treatment_metrics=metric_values,
            sham_control_metrics=metric_values,
            unrelated_position_control_metrics=metric_values,
            randomization_baseline_metrics=metric_values,
            control_output_artifact_ref="artifact://sha256/" + "1" * 64,
            treatment_output_artifact_ref="artifact://sha256/" + "2" * 64,
            sham_control_output_artifact_ref="artifact://sha256/" + "3" * 64,
            unrelated_position_control_output_artifact_ref="artifact://sha256/" + "4" * 64,
            randomization_baseline_experiment_id=f"{prefix}-randomization",
            randomization_baseline_output_artifact_ref="artifact://sha256/" + "5" * 64,
            intervention_result=InterventionResult(
                intervention_id=f"intervention-{seed}", status="completed", changed=True,
                effect_status="measured",
                before_artifact_ref="artifact://sha256/" + "6" * 64,
                after_artifact_ref="artifact://sha256/" + "7" * 64,
            ),
        ))
    return PairedExperimentExecutionReport(
        run_id=run_id,
        experiment_id=request.experiment_id,
        design_id=design.design_id,
        hypothesis_id=design.hypothesis_id,
        status="completed",
        evidence_mode="measured",
        causal_claim_status="causal_validation_required",
        design_fingerprint=design.design_fingerprint,
        preregistration_artifact_ref=design.preregistration_artifact_ref,
        preregistration_sha256=design.preregistration_sha256,
        model_revision=design.model_revision,
        decoding_settings_sha256=design.decoding_settings_sha256,
        control_condition=design.control_condition,
        treatment_condition=design.treatment_condition,
        control_operation=design.control_operation,
        treatment_operation=design.treatment_operation,
        sham_control_operation=design.sham_control_operation,
        unrelated_position_control_operation=design.negative_control_operation,
        randomization_or_permutation_baseline=design.randomization_or_permutation_baseline,
        hidden_truth_excluded_from_model_input=True,
        execution_log_artifact_ref="artifact://sha256/" + "b" * 64,
        execution_log_sha256="b" * 64,
        intervention_parameters_artifact_ref="artifact://sha256/" + "c" * 64,
        intervention_parameters_sha256="c" * 64,
        evaluator_version="evaluator-1.2.0",
        statistical_report_artifact_ref="artifact://sha256/" + "d" * 64,
        independent_case_family_ids=design.independent_case_family_ids,
        requested_pairs=design.repetitions,
        completed_pairs=design.repetitions,
        pairs=pairs,
    )


def test_real_paired_runner_requires_complete_preregistration_before_submission():
    backend, request, report, scenario, design, template = _prepared_run()
    calls = []

    class RealModeWrapper:
        def capabilities(self):
            return backend.capabilities().model_copy(update={"backend_mode": "real"})

    def executor(**kwargs):
        calls.append(kwargs)


    with pytest.raises(PairedExecutionUnsupported, match="pre-registered"):
        PairedExperimentRunner(
            RealModeWrapper(), executor,
            preregistration_verifier=lambda _: True,
            output_artifact_verifier=lambda _: True,
        ).run(
            run_id=report.run_id,
            scenario=scenario,
            base_request=request,
            design=design,
            intervention_template=template.model_copy(update={
                "parameters": {"scope": "isolated_test_environment"},
            }),
            approval_granted=True,
        )
    assert calls == []


def test_real_paired_runner_requires_all_control_arms_and_keeps_causal_claim_gated():
    backend, request, report, scenario, design, template = _prepared_run()
    request = request.model_copy(update={"model_version": "model@revision-1"})
    design = _preregistered_design(design, request, scenario, template)
    real_template = template.model_copy(update={
        "parameters": {"scope": "isolated_test_environment"},
    })

    class RealModeWrapper:
        def capabilities(self):
            return backend.capabilities().model_copy(update={"backend_mode": "real"})

    valid = _real_executor_report(report.run_id, request, design)
    executor_calls = []

    def submit_once(**kwargs):
        executor_calls.append(kwargs)
        return valid

    with pytest.raises(PairedExecutionUnsupported, match="trusted pre-registration"):
        PairedExperimentRunner(
            RealModeWrapper(), submit_once, output_artifact_verifier=lambda _: True,
        ).run(
            run_id=report.run_id,
            scenario=scenario,
            base_request=request,
            design=design,
            intervention_template=real_template,
            approval_granted=True,
        )
    with pytest.raises(PairedExecutionUnsupported, match="output-artifact verifier"):
        PairedExperimentRunner(
            RealModeWrapper(), submit_once, preregistration_verifier=lambda _: True,
        ).run(
            run_id=report.run_id,
            scenario=scenario,
            base_request=request,
            design=design,
            intervention_template=real_template,
            approval_granted=True,
        )
    with pytest.raises(PairedExecutionUnsupported, match="unverified"):
        PairedExperimentRunner(
            RealModeWrapper(), submit_once,
            preregistration_verifier=lambda _: False,
            output_artifact_verifier=lambda _: True,
        ).run(
            run_id=report.run_id,
            scenario=scenario,
            base_request=request,
            design=design,
            intervention_template=real_template,
            approval_granted=True,
        )
    assert executor_calls == []

    result = PairedExperimentRunner(
        RealModeWrapper(), submit_once,
        preregistration_verifier=lambda _: True,
        output_artifact_verifier=lambda _: True,
    ).run(
        run_id=report.run_id,
        scenario=scenario,
        base_request=request,
        design=design,
        intervention_template=real_template,
        approval_granted=True,
    )
    assert result.status == "completed"
    assert result.causal_claim_status == "causal_validation_required"

    rejected_artifact_calls = []

    def return_report(**kwargs):
        rejected_artifact_calls.append(kwargs)
        return valid

    with pytest.raises(ValueError, match="trusted content/ownership"):
        PairedExperimentRunner(
            RealModeWrapper(), return_report,
            preregistration_verifier=lambda _: True,
            output_artifact_verifier=lambda _: False,
        ).run(
            run_id=report.run_id,
            scenario=scenario,
            base_request=request,
            design=design,
            intervention_template=real_template,
            approval_granted=True,
        )
    assert len(rejected_artifact_calls) == 1

    missing_sham = valid.model_copy(deep=True)
    missing_sham.pairs[0].sham_control_status = "not_run"
    with pytest.raises(ValueError, match="sham"):
        PairedExperimentRunner(
            RealModeWrapper(), lambda **_: missing_sham,
            preregistration_verifier=lambda _: True,
            output_artifact_verifier=lambda _: True,
        ).run(
            run_id=report.run_id,
            scenario=scenario,
            base_request=request,
            design=design,
            intervention_template=real_template,
            approval_granted=True,
        )
