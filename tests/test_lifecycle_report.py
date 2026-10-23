from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine


def test_report_contains_timeline_capabilities_and_budget():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(
        ExperimentRequest(scenario_id="demo_cloud_security")
    )
    assert report.capability_manifest is not None
    assert report.capability_manifest.provider_id == "mock"
    assert report.timeline
    assert report.timeline[0].stage == "created"
    assert report.budget_usage is not None
    assert report.budget_usage.observations_collected == 3
    assert report.budget_usage.findings_generated >= 1
    assert report.budget_usage.intervention_attempts == 1
    assert report.timeline[-1].stage in {"completed", "partial"}
