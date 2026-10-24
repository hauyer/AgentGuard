import pytest

from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.intervention import InterventionRequest
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
