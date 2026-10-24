from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.graph import InteractionGraphAnalyzer, MultiLayerDetector
from agentguard.core.orchestrator import AgentGuardEngine


def test_graph_analysis_is_evidence_linked_and_does_not_claim_causality():
    backend = MockBackend()
    observations = backend.execute(backend.get_scenario("demo_cloud_security"), ExperimentRequest(scenario_id="demo_cloud_security"))
    graph = InteractionGraphAnalyzer().analyze(observations)
    assert graph.node_count >= 2
    assert graph.edge_count == len(observations)
    assert graph.suspicious_edge_count >= 1
    suspicious = [edge for edge in graph.edges if edge.risk_reasons]
    assert all(edge.evidence_refs for edge in suspicious)
    assert graph.limitations


def test_multilayer_detector_keeps_unique_rule_and_graph_findings():
    backend = MockBackend()
    observations = backend.execute(backend.get_scenario("demo_cloud_security"), ExperimentRequest(scenario_id="demo_cloud_security"))
    findings = MultiLayerDetector().detect(observations)
    keys = [(item.target_ref, item.risk_type) for item in findings]
    assert len(keys) == len(set(keys))
    assert any(item.target_ref == "msg-002" for item in findings)


def test_run_report_includes_graph_analysis():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(scenario_id="demo_cloud_security"))
    assert report.graph_analysis is not None
    assert report.graph_analysis.edge_count == len(report.observations)


def test_behavioral_detector_flags_tool_fanout_as_a_heuristic_candidate():
    from agentguard.contracts.observation import Observation
    from agentguard.core.graph import BehavioralDetector

    observations = [
        Observation(
            experiment_id="exp-fanout", actor_id="agent-a", event_type="tool_call",
            source="agent-a", target=f"tool-{i}", target_ref=f"call-{i}",
            summary="Observed tool invocation", features={}, provenance="test://trace",
        )
        for i in range(4)
    ]
    findings = BehavioralDetector(min_tool_fanout=4).detect(observations)
    assert len(findings) == 1
    assert findings[0].risk_type == "behavioral_tool_fanout_anomaly"
    assert findings[0].target_type == "tool_call"
    assert "not proof" in findings[0].rationale


def test_semantic_detector_is_opt_in_and_structured():
    from agentguard.core.graph import MultiLayerDetector
    backend = MockBackend()
    observations = backend.execute(backend.get_scenario("demo_cloud_security"), ExperimentRequest(scenario_id="demo_cloud_security"))
    target = observations[0]
    target.target_ref = "semantic-target"
    detector = MultiLayerDetector(semantic_scorer=lambda obs: {
        "score": 0.91, "risk_type": "semantic_policy_conflict",
        "rationale": "The content requests a tool action outside the declared purpose.",
        "alternative_explanations": ["The task may have implicit authorization."],
    })
    findings = detector.detect([target])
    assert "semantic_callback" in detector.layer_names
    assert any(item.risk_type == "semantic_policy_conflict" for item in findings)
