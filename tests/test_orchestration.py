from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine


def test_demo_runs_end_to_end_but_marks_synthetic():
    backend = MockBackend()
    engine = AgentGuardEngine(backend, backend, backend)
    report = engine.run(ExperimentRequest(scenario_id="demo_cloud_security"))
    assert report.status == "completed"
    assert report.backend_mode == "synthetic"
    assert len(report.findings) >= 1
    assert report.intervention_result is not None
    assert report.intervention_result.effect_status == "simulated"
    assert report.causal_claim_status == "simulated_only"
    assert report.evaluation is not None
    assert report.evaluation.evidence_mode == "synthetic"
    assert any("Synthetic backend only" in item for item in report.limitations)


def test_zero_budget_does_not_intervene():
    backend = MockBackend()
    engine = AgentGuardEngine(backend, backend, backend)
    report = engine.run(
        ExperimentRequest(scenario_id="demo_cloud_security", max_interventions=0)
    )
    assert report.findings
    assert report.selected_intervention is None
    assert report.intervention_result is None
    assert report.causal_claim_status == "not_assessed"


def test_unknown_scenario_fails_explicitly():
    backend = MockBackend()
    engine = AgentGuardEngine(backend, backend, backend)
    try:
        engine.run(ExperimentRequest(scenario_id="not-real"))
        assert False, "expected KeyError"
    except KeyError as exc:
        assert "Unknown scenario_id" in str(exc)
