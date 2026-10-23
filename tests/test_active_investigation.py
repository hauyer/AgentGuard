import pytest
from pydantic import ValidationError
from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.observation import Observation
from agentguard.core.orchestrator import AgentGuardEngine


def test_observation_rejects_known_hidden_truth_fields():
    with pytest.raises(ValidationError):
        Observation(experiment_id="x", actor_id="a", event_type="message", summary="x",
                    features={"oracle_label": True})


def test_run_includes_hypotheses_paired_design_and_defense_preview_without_claiming_execution():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security", runtime_backend="mock", auto_intervene=False,
        max_interventions=2, seed=13,
    ))
    assert report.observations
    assert report.findings
    assert report.hypotheses
    assert report.investigation_actions
    assert report.experiment_designs[0].seeds == [13, 14, 15, 16, 17]
    assert report.defense_preview is not None
    assert report.defense_preview.execution_performed is False
    assert report.intervention_result is None
    assert any("not proof" in item for item in report.limitations)


def test_read_only_policy_profile_never_executes_intervention():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security", runtime_backend="mock", policy_profile="read_only",
        auto_intervene=True, max_interventions=3,
    ))
    assert report.selected_intervention is None
    assert report.intervention_result is None
    assert report.budget_usage.intervention_attempts == 0


def test_pre_cancelled_run_returns_partial_auditable_report():
    from threading import Event
    backend = MockBackend()
    event = Event(); event.set()
    report = AgentGuardEngine(backend, backend, backend).run(
        ExperimentRequest(scenario_id="demo_cloud_security"), cancel_event=event
    )
    assert report.status == "partial"
    assert report.observations == []
    assert any("Cancellation" in item for item in report.limitations)


def test_read_only_policy_profile_never_executes_intervention():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security", runtime_backend="mock", policy_profile="read_only",
        auto_intervene=True, max_interventions=3,
    ))
    assert report.selected_intervention is None
    assert report.intervention_result is None
    assert report.budget_usage.intervention_attempts == 0


def test_pre_cancelled_run_returns_partial_auditable_report():
    from threading import Event
    backend = MockBackend()
    event = Event(); event.set()
    report = AgentGuardEngine(backend, backend, backend).run(
        ExperimentRequest(scenario_id="demo_cloud_security"), cancel_event=event
    )
    assert report.status == "partial"
    assert report.observations == []
    assert any("Cancellation" in item for item in report.limitations)


def test_observation_rejects_generic_truth_and_label_keys():
    with pytest.raises(ValidationError):
        Observation(experiment_id="x", actor_id="a", event_type="message", summary="x",
                    features={"truth": "secret-answer"})
