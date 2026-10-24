"""Contract tests for the message-level interaction graph API (C1)."""

import pytest
from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.interaction_graph import GraphEdge, GraphNode, InteractionGraphResponse
from agentguard.contracts.observation import DetectionFinding, Observation
from agentguard.contracts.report import RunReport
from agentguard.services.interaction_graph import InteractionGraphService


def _workflow_entry(**overrides):
    """A journal entry shaped like the real 7B observation runs."""
    run = {
        "run_id": "run-real-1",
        "plan_id": "plan-real-1",
        "status": "completed",
        "created_at": "2026-10-23T16:32:37.245948+00:00",
        "updated_at": "2026-10-23T16:33:28.146427+00:00",
        "evidence": {
            "findings": [
                {
                    "kind": "observational_run",
                    "evidence_level": "observational",
                    "messages": [
                        {"message_id": "msg-000001", "sender": "performance_reviewer",
                         "recipient": "release_chair", "round_index": 0},
                        {"message_id": "msg-000002", "sender": "security_reviewer",
                         "recipient": "release_chair", "round_index": 0},
                    ],
                }
            ],
            "artifacts": [
                {"artifact_id": "a" * 64, "uri": "file:///D:/never/opened.bin",
                 "kind": "observation-summary"},
            ],
        },
    }
    entry = {
        "request_id": "inv-graph-1",
        "status": "finished",
        "workflow_status": "completed",
        "request": {"scenario_id": "sc-release", "model_id": "model-7b"},
        "context": {"run": run, "plan": {"plan_id": "plan-real-1"}},
        "result": {"status": "completed", "run": run},
        "run_id": "run-real-1",
    }
    entry.update(overrides)
    return entry


def _synthetic_report() -> RunReport:
    observations = [
        Observation(
            experiment_id="exp-1", actor_id="security_agent_a", event_type="message",
            source="security_agent_a", target="report_agent_b", target_ref="msg-001",
            summary="Synthetic message one.",
        ),
        Observation(
            experiment_id="exp-1", actor_id="report_agent_b", event_type="tool_call",
            source="report_agent_b", target="mcp_lookup", target_ref="tool-call-002",
            summary="Synthetic tool call.",
        ),
        Observation(
            experiment_id="exp-1", actor_id="mcp_lookup", event_type="tool_result",
            source="mcp_lookup", target="report_agent_b", target_ref="tool-result-002",
            summary="Synthetic tool result.",
        ),
        Observation(
            experiment_id="exp-1", actor_id="hidden_agent", event_type="message",
            source="hidden_agent", target="report_agent_b", target_ref="msg-private",
            summary="Private agent state.", visibility="agent_private",
        ),
    ]
    findings = [
        DetectionFinding(
            risk_type="unauthorized_flow", severity="high", target_type="message",
            target_ref="msg-001", score=0.9, rationale="synthetic screening signal",
            evidence_refs=["artifact://sha256/" + "b" * 64],
        )
    ]
    return RunReport(
        run_id="run-syn-1", experiment_id="exp-1", scenario_id="sc-demo",
        status="completed", backend_mode="synthetic",
        observations=observations, findings=findings,
    )


# --------------------------------------------------------------------------- #
# service: real WARDEN observation source
# --------------------------------------------------------------------------- #

def test_real_structured_messages_project_to_agent_nodes_and_message_edges():
    graph = InteractionGraphService().for_warden_workflow(_workflow_entry())
    assert isinstance(graph, InteractionGraphResponse)
    assert graph.available is True
    assert graph.data_status == "complete"
    assert graph.source == "warden_observation"
    assert graph.run_id == "run-real-1"
    assert graph.scenario_id == "sc-release"
    node_ids = {node.id for node in graph.nodes}
    assert node_ids == {
        "agent:performance_reviewer", "agent:security_reviewer", "agent:release_chair",
    }
    assert all(node.type == "agent" for node in graph.nodes)
    assert all(node.source_id for node in graph.nodes)
    assert len(graph.edges) == 2
    for edge in graph.edges:
        assert edge.type == "message"
        assert edge.message_id
        assert edge.source_id == edge.message_id
        assert edge.evidence_refs == ["a" * 64]  # file:// URI dropped, artifact_id kept


def test_partial_metadata_degrades_to_partial_with_limitation():
    entry = _workflow_entry()
    finding = entry["context"]["run"]["evidence"]["findings"][0]
    finding["messages"].append(
        {"message_id": "msg-000003", "sender": " ", "recipient": "release_chair"}
    )
    finding["messages"].append({"recipient": "release_chair"})
    graph = InteractionGraphService().for_warden_workflow(entry)
    assert graph.available is True
    assert graph.data_status == "partial"
    assert len(graph.edges) == 2
    assert any("lacked sender, recipient or message_id" in note for note in graph.limitations)


def test_duplicate_messages_collapse_and_conflicting_ids_are_dropped():
    entry = _workflow_entry()
    finding = entry["context"]["run"]["evidence"]["findings"][0]
    finding["messages"].append(
        {"message_id": "msg-000001", "sender": "performance_reviewer",
         "recipient": "release_chair", "round_index": 0}
    )
    finding["messages"].append(
        {"message_id": "msg-000001", "sender": "security_reviewer",
         "recipient": "release_chair", "round_index": 1}
    )
    graph = InteractionGraphService().for_warden_workflow(entry)
    assert len(graph.edges) == 2
    assert graph.data_status == "partial"
    assert any("duplicate message entries were collapsed" in note for note in graph.limitations)
    assert any("conflicted with an earlier entry" in note for note in graph.limitations)


def test_out_of_order_rounds_are_sorted_by_round_index():
    entry = _workflow_entry()
    finding = entry["context"]["run"]["evidence"]["findings"][0]
    finding["messages"] = [
        {"message_id": "msg-late", "sender": "security_reviewer",
         "recipient": "release_chair", "round_index": 5},
        {"message_id": "msg-early", "sender": "performance_reviewer",
         "recipient": "release_chair", "round_index": 1},
    ]
    graph = InteractionGraphService().for_warden_workflow(entry)
    assert [edge.message_id for edge in graph.edges] == ["msg-early", "msg-late"]


def test_workflow_without_run_payload_reports_unavailable():
    entry = _workflow_entry(context={}, result={"status": "blocked"}, run_id=None)
    graph = InteractionGraphService().for_warden_workflow(entry)
    assert graph.available is False
    assert graph.data_status == "unavailable"
    assert graph.source == "none"
    assert any("GRAPH_SOURCE_UNAVAILABLE" in note for note in graph.limitations)
    assert graph.nodes == [] and graph.edges == []


def test_run_without_message_metadata_reports_unavailable_not_fabricated():
    entry = _workflow_entry()
    run = entry["context"]["run"]
    run["evidence"] = {"findings": [{"kind": "observational_run"}], "artifacts": []}
    graph = InteractionGraphService().for_warden_workflow(entry)
    assert graph.available is False
    assert graph.data_status == "unavailable"
    assert graph.edges == []
    assert any("GRAPH_SOURCE_UNAVAILABLE" in note for note in graph.limitations)


def test_candidate_paths_stay_empty_without_verified_risk_association():
    graph = InteractionGraphService().for_warden_workflow(_workflow_entry())
    assert graph.candidate_paths == []
    assert any("no candidate path is inferred from risk scores" in note for note in graph.limitations)


def test_hidden_truth_payloads_never_reach_the_projection():
    entry = _workflow_entry()
    entry["context"]["run"]["evidence"]["findings"][0]["messages"].append(
        {"message_id": "msg-truth", "sender": "oracle", "recipient": "release_chair",
         "ground_truth_label": "positive"}
    )
    graph = InteractionGraphService().for_warden_workflow(entry)
    dumped = graph.model_dump_json()
    assert "ground_truth" not in dumped
    assert "oracle_label" not in dumped
    assert "msg-truth" in dumped  # the edge itself is recorded metadata, not truth
    with pytest.raises(ValueError):
        GraphNode(
            id="n", type="agent", label="n", source_id="s", metadata={"ground_truth": 1}
        )
    with pytest.raises(ValueError):
        GraphEdge(
            id="e", source="a", target="b", type="message", source_id="s",
            metadata={"oracle_label": "x"},
        )


# --------------------------------------------------------------------------- #
# service: synthetic engine report source
# --------------------------------------------------------------------------- #

def test_synthetic_report_projects_agent_agent_message_and_tool_edges():
    graph = InteractionGraphService().for_run_report(_synthetic_report(), request_id="inv-x")
    assert graph.available is True
    assert graph.source == "agentguard_synthetic_report"
    edge_types = {(edge.source, edge.target, edge.type) for edge in graph.edges}
    assert ("agent:security_agent_a", "agent:report_agent_b", "message") in edge_types
    assert ("agent:report_agent_b", "tool:mcp_lookup", "invocation") in edge_types
    assert ("tool:mcp_lookup", "agent:report_agent_b", "result") in edge_types
    assert all(edge.timestamp for edge in graph.edges)
    assert graph.candidate_paths == []
    assert any("synthetic research engine" in note for note in graph.limitations)


def test_synthetic_graph_is_never_labelled_as_real_observation():
    graph = InteractionGraphService().for_run_report(_synthetic_report(), request_id="inv-x")
    assert graph.source != "warden_observation"
    assert "scripted demo events" in " ".join(graph.limitations)


def test_agent_private_events_are_filtered_from_the_public_graph():
    graph = InteractionGraphService().for_run_report(_synthetic_report(), request_id="inv-x")
    assert all("hidden_agent" not in edge.source for edge in graph.edges)
    assert all(node.id != "agent:hidden_agent" for node in graph.nodes)
    assert graph.data_status == "partial"
    assert any("agent-private" in note for note in graph.limitations)


def test_finding_severity_maps_to_edge_and_node_risk_with_evidence_refs():
    graph = InteractionGraphService().for_run_report(_synthetic_report(), request_id="inv-x")
    risky = next(edge for edge in graph.edges if edge.message_id == "msg-001")
    assert risky.risk == "high"
    assert risky.evidence_refs == ["artifact://sha256/" + "b" * 64]
    nodes = {node.id: node for node in graph.nodes}
    assert nodes["agent:security_agent_a"].risk == "high"
    assert nodes["agent:report_agent_b"].risk == "high"
    unrisky = next(edge for edge in graph.edges if edge.message_id == "tool-call-002")
    assert unrisky.risk == "unknown"


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #

def _seeded_client(tmp_path, entry=None):
    app = create_app(data_dir=tmp_path / "data")
    if entry is not None:
        app.state.warden_workflows.begin(
            entry["request_id"],
            {"scenario_id": "sc-release", "model_id": "model-7b"},
        )
        app.state.warden_workflows.update(
            entry["request_id"], "finished",
            context=entry["context"], result=entry["result"],
        )
    return app


def test_api_returns_real_graph_for_seeded_workflow(tmp_path):
    entry = _workflow_entry()
    app = _seeded_client(tmp_path, entry)
    with TestClient(app) as client:
        response = client.get(f"/v1/investigations/{entry['request_id']}/interaction-graph")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["available"] is True
    assert body["source"] == "warden_observation"
    assert len(body["edges"]) == 2
    assert response.headers.get("X-Request-ID")


def test_api_reports_unavailable_when_journal_has_no_messages(tmp_path):
    entry = _workflow_entry()
    entry["context"]["run"]["evidence"]["findings"] = []
    app = _seeded_client(tmp_path, entry)
    with TestClient(app) as client:
        response = client.get(f"/v1/investigations/{entry['request_id']}/interaction-graph")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["data_status"] == "unavailable"
    assert any("GRAPH_SOURCE_UNAVAILABLE" in note for note in body["limitations"])


def test_api_falls_back_to_explicitly_requested_synthetic_run(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    scenario_id = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    request = ExperimentRequest(scenario_id=scenario_id)
    report = app.state.engine.run(request)
    app.state.reports[report.run_id] = report
    with TestClient(app) as client:
        response = client.get(
            "/v1/investigations/inv-none/interaction-graph", params={"run_id": report.run_id}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["source"] == "agentguard_synthetic_report"


def test_api_404_for_unknown_workflow_without_run_id(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.get("/v1/investigations/inv-missing/interaction-graph")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "WORKFLOW_NOT_FOUND"
        missing_run = client.get(
            "/v1/investigations/inv-missing/interaction-graph",
            params={"run_id": "run-missing"},
        )
        assert missing_run.status_code == 404
        assert missing_run.json()["detail"]["error"] == "RUN_ID_NOT_FOUND"


def test_openapi_registers_the_graph_route(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        schema = client.get("/openapi.json").json()
    route = schema["paths"].get("/v1/investigations/{request_id}/interaction-graph")
    assert route is not None and "get" in route
