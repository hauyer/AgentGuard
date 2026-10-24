from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine


def test_run_contains_typed_role_messages_and_verifier_summary():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(
        ExperimentRequest(scenario_id="demo_cloud_security")
    )
    assert report.agent_messages
    roles = {message.sender_role for message in report.agent_messages}
    assert {"supervisor", "trace_analyst", "causal_investigator", "defense_planner"} <= roles
    assert "verifier" in {message.recipient_role for message in report.agent_messages}
    assert any(message.message_type == "policy_decision" for message in report.agent_messages)
    assert any(message.message_type == "verification_summary" for message in report.agent_messages)
    assert all("truth" not in message.payload for message in report.agent_messages)
    assert all(message.run_id == report.run_id for message in report.agent_messages)


def test_parallel_runs_have_isolated_coordination_traces():
    from concurrent.futures import ThreadPoolExecutor

    backend = MockBackend()
    engine = AgentGuardEngine(backend, backend, backend)

    def run_one(index):
        return engine.run(
            ExperimentRequest(
                experiment_id=f"parallel-{index}",
                scenario_id="demo_cloud_security",
                runtime_backend="mock",
            )
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(run_one, range(8)))

    assert len({report.run_id for report in reports}) == 8
    for report in reports:
        assert report.agent_messages
        assert all(message.run_id == report.run_id for message in report.agent_messages)


def test_llm_advisor_can_prioritize_existing_finding_but_only_emits_audit_advice():
    from agentguard.agents.coordinator import AgentCoordinator
    from agentguard.contracts.observation import DetectionFinding

    class FakeAdvisor:
        def __init__(self, selected):
            self.selected = selected
            self.last_decision = {"status": "advice_received", "provider": "fake", "model": "test-model",
                                  "selected_finding_id": selected, "rationale": "Choose evidence-rich candidate.",
                                  "confidence": 0.7, "model_calls_used": 1, "model_tokens_used": 6}

        def new_run(self):
            return FakeAdvisor(self.selected)

        def select_finding(self, findings, *, max_tokens=384, max_candidates=25):
            assert max_tokens > 0 and max_candidates > 0
            return self.selected

    first = DetectionFinding(finding_id="finding-a", risk_type="risk-a", severity="high",
                             target_type="message", target_ref="msg-a", score=0.99,
                             rationale="High score but limited evidence.", evidence_refs=["event-a"])
    second = DetectionFinding(finding_id="finding-b", risk_type="risk-b", severity="medium",
                              target_type="message", target_ref="msg-b", score=0.7,
                              rationale="Multiple independent event references.", evidence_refs=["event-b", "event-c"])
    coordinator = AgentCoordinator(advisor=FakeAdvisor("finding-b")).new_run()
    coordinator.begin("run-llm-test", "scenario-test", 1)
    selected = coordinator.investigate([first, second], max_interventions=1, preferred_finding_id="finding-a")
    assert selected.finding_id == "finding-b"
    advice = next(message for message in coordinator.messages if message.message_type == "llm_investigation_advice")
    assert advice.payload["selected_finding_id"] == "finding-b"
    assert advice.evidence_refs == ["event-a", "event-b", "event-c"]
    assert coordinator.model_calls_used == 1
    assert coordinator.model_tokens_used == 6
    assert not any(message.message_type == "execute_tool" for message in coordinator.messages)


def test_llm_strategy_budget_unknown_falls_back_and_records_reason():
    from agentguard.agents.coordinator import AgentCoordinator
    from agentguard.contracts.observation import DetectionFinding

    candidate = DetectionFinding(
        finding_id="finding-budget", risk_type="risk", severity="high", target_type="message",
        target_ref="msg-budget", score=0.8, rationale="Candidate without a calibrated causal claim.",
        evidence_refs=["event-budget"],
    )
    coordinator = AgentCoordinator(advisor=object()).new_run()
    coordinator.begin("run-budget-fallback", "scenario-test", 1)
    selected = coordinator.investigate(
        [candidate],
        max_interventions=1,
        strategy="llm",
        max_model_calls_remaining=None,
        max_model_tokens_remaining=None,
    )
    advice = next(
        message for message in coordinator.messages
        if message.message_type == "llm_investigation_advice"
    )
    assert selected is candidate
    assert advice.payload["status"] == "model_budget_unknown_fallback"
    assert advice.payload["fallback_reason"] == "model_budget_unknown_fallback_to_information_value"
    assert coordinator.model_calls_used == 0
