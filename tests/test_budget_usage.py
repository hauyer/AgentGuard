from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine


class OverBudgetMock(MockBackend):
    def usage_for(self, experiment_id: str):
        return {
            "model_calls_used": 70,
            "model_tokens_used": 51000,
            "tool_calls_used": 42,
        }


def test_observed_usage_over_budget_marks_run_partial():
    backend = OverBudgetMock()
    report = AgentGuardEngine(backend, backend, backend).run(
        ExperimentRequest(
            scenario_id="demo_cloud_security",
            runtime_backend="mock",
            max_model_calls=64,
            max_model_tokens=50000,
            max_tool_calls=40,
        )
    )
    assert report.status == "partial"
    assert report.budget_usage is not None
    assert report.budget_usage.model_calls_used == 70
    assert report.budget_usage.model_token_budget_exceeded
    assert report.budget_usage.tool_call_budget_exceeded
