from agentguard.adapters.mock import MockBackend
from agentguard.contracts.intervention import InterventionRequest


def test_mock_rejects_activation_intervention():
    backend = MockBackend()
    result = backend.intervene(
        InterventionRequest(
            experiment_id="exp-1",
            intervention_id="int-1",
            target_type="activation",
            target_ref="layer.10.resid_post",
            operation="patch",
        )
    )
    assert result.status == "unsupported"
    assert result.error_code == "CAPABILITY_UNAVAILABLE"
    assert result.effect_status == "not_measured"


def test_mock_rejects_unknown_target():
    backend = MockBackend()
    result = backend.intervene(
        InterventionRequest(
            experiment_id="exp-1",
            intervention_id="int-2",
            target_type="message",
            target_ref="not-in-demo",
            operation="block",
        )
    )
    assert result.status == "rejected"
    assert result.error_code == "TARGET_NOT_FOUND"
