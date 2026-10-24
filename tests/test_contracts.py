import pytest
from pydantic import ValidationError

from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.intervention import InterventionRequest
from agentguard.contracts.scenario import ScenarioSpec
from agentguard.contracts.warden import WardenAuditRequest


def test_scenario_rejects_unexpected_truth_field():
    with pytest.raises(ValidationError):
        ScenarioSpec(
            scenario_id="x",
            name="x",
            description="x",
            truth={"secret": "must-not-be-in-agent-context"},
        )


def test_intervention_request_requires_target():
    with pytest.raises(ValidationError):
        InterventionRequest(
            experiment_id="exp-1",
            intervention_id="int-1",
            target_type="message",
            target_ref="",
            operation="block",
        )


def test_nested_experiment_budget_uses_defaults_for_unspecified_fields():
    request = ExperimentRequest.model_validate({
        "scenario_id": "demo_cloud_security",
        "budget": {"max_interventions": 3},
    })
    assert request.max_interventions == 3
    assert request.max_runtime_seconds == 120
    assert request.max_model_calls == 64


def test_conflicting_flat_runtime_and_nested_budget_is_rejected():
    with pytest.raises(ValidationError):
        ExperimentRequest.model_validate({
            "scenario_id": "demo_cloud_security",
            "max_runtime_seconds": 60,
            "budget": {"max_runtime_seconds": 90},
        })


def test_nested_experiment_budget_is_accepted_and_flattened():
    request = ExperimentRequest.model_validate({
        "scenario_id": "demo_cloud_security",
        "budget": {"max_interventions": 3, "max_runtime_seconds": 90, "max_model_calls": 12},
    })
    assert request.max_interventions == 3
    assert request.max_runtime_seconds == 90
    assert request.max_model_calls == 12


def test_conflicting_flat_and_nested_budget_is_rejected():
    with pytest.raises(ValidationError):
        ExperimentRequest.model_validate({
            "scenario_id": "demo_cloud_security",
            "max_interventions": 1,
            "budget": {"max_interventions": 3},
        })


@pytest.mark.parametrize("metadata", [
    {"oracle_label": "hidden-sentinel"},
    {"nested": [{"groundTruthLabel": "hidden-sentinel"}]},
    {"answer-key": "hidden-sentinel"},
])
def test_warden_audit_request_rejects_nested_hidden_truth_metadata(metadata):
    with pytest.raises(ValidationError):
        WardenAuditRequest(
            request_id="truth-boundary",
            contract_id="contract-1",
            model_id="model-1",
            runtime_id="runtime-1",
            metadata=metadata,
        )
