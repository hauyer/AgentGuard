import pytest

from agentguard.adapters.mock import MockBackend
from agentguard.contracts.intervention import InterventionRequest
from agentguard.core.policy import InterventionPolicyEngine


def _request(**overrides):
    values = {
        "experiment_id": "exp-1",
        "intervention_id": "int-1",
        "target_type": "message",
        "target_ref": "msg-002",
        "operation": "block",
        "parameters": {
            "scope": "synthetic_demo",
            "approval_required": True,
        },
    }
    values.update(overrides)
    return InterventionRequest(**values)


def test_mock_capabilities_are_explicit_and_allow_demo_action():
    backend = MockBackend()
    manifest = backend.capabilities()
    assert manifest.backend_mode == "synthetic"
    assert manifest.supports("message_trace")
    assert manifest.supports_intervention("message", "block")
    decision = InterventionPolicyEngine().evaluate(
        backend.get_scenario("demo_cloud_security"),
        _request(),
        manifest,
        max_interventions=1,
        interventions_already_attempted=0,
    )
    assert decision.allowed


def test_policy_rejects_target_not_allowed_by_scenario():
    backend = MockBackend()
    request = _request(target_type="activation", operation="patch")
    decision = InterventionPolicyEngine().evaluate(
        backend.get_scenario("demo_cloud_security"),
        request,
        backend.capabilities(),
        max_interventions=1,
        interventions_already_attempted=0,
    )
    assert decision.status == "rejected"
    assert decision.error_code == "POLICY_DENIED"


def test_policy_rejects_exhausted_budget():
    backend = MockBackend()
    decision = InterventionPolicyEngine().evaluate(
        backend.get_scenario("demo_cloud_security"),
        _request(),
        backend.capabilities(),
        max_interventions=1,
        interventions_already_attempted=1,
    )
    assert decision.error_code == "BUDGET_EXCEEDED"


def test_real_backend_requires_approval_for_high_impact_request():
    from agentguard.contracts.capabilities import CapabilityManifest

    backend = MockBackend()
    manifest = CapabilityManifest(
        provider_id="test-real",
        backend_mode="real",
        capabilities={"message_trace": True},
        supported_target_types=["message"],
        supported_operations=["block"],
    )
    decision = InterventionPolicyEngine().evaluate(
        backend.get_scenario("demo_cloud_security"),
        _request(parameters={"scope": "isolated_test_environment", "approval_required": True}, authorization_ref="approval-ticket-123"),
        manifest,
        max_interventions=1,
        interventions_already_attempted=0,
        approval_granted=False,
    )
    assert decision.status == "approval_required"
    assert decision.error_code == "APPROVAL_REQUIRED"


@pytest.mark.parametrize("approval_granted", [True, False])
def test_real_backend_requires_authorization_reference_even_if_approval_is_granted(approval_granted):
    from agentguard.contracts.capabilities import CapabilityManifest
    backend = MockBackend()
    manifest = CapabilityManifest(
        provider_id="test-real", backend_mode="real",
        supported_target_types=["message"], supported_operations=["block"],
    )
    decision = InterventionPolicyEngine().evaluate(
        backend.get_scenario("demo_cloud_security"),
        _request(parameters={"scope": "isolated_test_environment"}), manifest,
        max_interventions=1, interventions_already_attempted=0,
        approval_granted=approval_granted,
    )
    assert decision.allowed is False
    assert decision.error_code == "AUTHORIZATION_REFERENCE_REQUIRED"

