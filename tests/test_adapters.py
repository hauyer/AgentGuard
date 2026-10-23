import pytest
from agentguard.adapters.fractal_bench import FractalBenchAdapter, IntegrationNotConfigured as FractalNotConfigured
from agentguard.adapters.stegopot import StegoPotAdapter
from agentguard.contracts.intervention import InterventionRequest


def test_fractal_adapter_fails_loudly_when_unconfigured():
    adapter = FractalBenchAdapter()
    with pytest.raises(FractalNotConfigured):
        adapter.get_scenario("core")


def test_stegopot_adapter_reports_intervention_as_unsupported_when_unconfigured():
    adapter = StegoPotAdapter()
    result = adapter.intervene(
        InterventionRequest(
            experiment_id="exp-1",
            intervention_id="int-1",
            target_type="message",
            target_ref="msg-1",
            operation="block",
            parameters={"scope": "isolated_test_environment"},
        )
    )
    assert result.status == "unsupported"
    assert result.error_code == "CAPABILITY_UNAVAILABLE"
