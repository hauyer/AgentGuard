"""Contract tests for metadata-based risk findings and hypotheses (D2)."""

import json

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.contracts.risk_findings import RiskAnalysisReport
from agentguard.services.risk_findings import RiskFindingService


def _entry(request_id="inv-risk", messages=None, findings_count=1):
    messages = messages if messages is not None else [
        {"message_id": "msg-1", "sender": "reviewer_a", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-2", "sender": "reviewer_b", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-3", "sender": "reviewer_c", "recipient": "chair", "round_index": 0},
    ]
    return {
        "request_id": request_id, "status": "finished", "workflow_status": "completed",
        "request": {"scenario_id": "sc-risk", "model_id": "model-7b"},
        "context": {"run": {
            "run_id": f"run-{request_id}", "plan_id": f"plan-{request_id}",
            "status": "completed",
            "evidence": {
                "findings": [{"kind": "observational_run", "messages": messages}],
                "artifacts": [{"artifact_id": "a" * 64,
                               "uri": "file:///D:/never/opened.json"}],
            },
        }},
        "result": {"status": "completed"},
        "run_id": f"run-{request_id}",
    }


def test_concentration_finding_with_falsifiable_hypothesis():
    report = RiskFindingService().analyze_workflow(_entry())
    assert isinstance(report, RiskAnalysisReport)
    assert report.available is True
    concentration = [f for f in report.findings
                     if f.detection_method == "collaboration_concentration"]
    assert len(concentration) == 1
    finding = concentration[0]
    assert finding.target_ref == "agent:chair"
    assert finding.supporting_evidence_refs == ["edge:msg-1", "edge:msg-2", "edge:msg-3"]
    assert finding.status == "candidate"
    assert finding.verification_status == "unverified"
    assert any("designed review topology" in note for note in finding.limitations)
    hypothesis = report.hypotheses[0]
    assert hypothesis.finding_id == finding.finding_id
    assert "unverified" in hypothesis.statement
    assert hypothesis.intervention_supported is False
    assert "activation.backend" in hypothesis.required_capabilities
    assert hypothesis.stop_condition
    # No causal wording may appear as a claim.
    text = json.dumps(report.model_dump(mode="json"))
    for banned in ("已确认", "confirmed causal", "causal mediator is proven"):
        assert banned not in text


def test_normal_sparse_messages_produce_no_concentration_finding():
    report = RiskFindingService().analyze_workflow(_entry(messages=[
        {"message_id": "msg-1", "sender": "a", "recipient": "b", "round_index": 0},
        {"message_id": "msg-2", "sender": "c", "recipient": "b", "round_index": 0},
    ]))
    assert not [f for f in report.findings
                if f.detection_method == "collaboration_concentration"]
    # Relay detection: none here (b never sends).
    assert not [f for f in report.findings if f.detection_method == "relay_path_candidate"]


def test_relay_chain_candidate_from_metadata_only():
    report = RiskFindingService().analyze_workflow(_entry(messages=[
        {"message_id": "msg-1", "sender": "externalist", "recipient": "relay_agent", "round_index": 0},
        {"message_id": "msg-2", "sender": "relay_agent", "recipient": "chair", "round_index": 1},
    ]))
    relays = [f for f in report.findings if f.detection_method == "relay_path_candidate"]
    assert len(relays) == 1
    assert relays[0].target_ref == "agent:relay_agent"
    assert sorted(relays[0].supporting_evidence_refs) == ["edge:msg-1", "edge:msg-2"]
    assert any("content was not read" in f.description for f in relays)


def test_out_of_order_rounds_flag_temporal_anomaly():
    report = RiskFindingService().analyze_workflow(_entry(messages=[
        {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 2},
        {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 1},
    ]))
    order = [f for f in report.findings if f.detection_method == "temporal_order_anomaly"]
    assert len(order) == 1
    assert order[0].target_ref == "edge:msg-2"


def test_missing_round_metadata_reports_insufficient_evidence_not_fabrication():
    report = RiskFindingService().analyze_workflow(_entry(messages=[
        {"message_id": "msg-1", "sender": "a", "recipient": "chair"},
        {"message_id": "msg-2", "sender": "b", "recipient": "chair"},
    ]))
    order = [f for f in report.findings if f.detection_method == "temporal_order_anomaly"]
    assert len(order) == 1
    assert order[0].status == "insufficient_evidence"
    assert order[0].calibration == "not_applicable"
    # Insufficient-evidence findings never spawn hypotheses.
    assert not any(h.finding_id == order[0].finding_id for h in report.hypotheses)


def test_incomplete_observation_without_messages_reports_unavailable():
    entry = _entry()
    entry["context"]["run"]["evidence"]["findings"] = []
    report = RiskFindingService().analyze_workflow(entry)
    assert report.available is False
    assert report.findings == [] and report.hypotheses == []
    assert any("No usable interaction metadata" in note for note in report.limitations)


def test_duplicate_messages_are_collapsed_before_detection():
    messages = [
        {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-3", "sender": "c", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 0},
    ]
    report = RiskFindingService().analyze_workflow(_entry(messages=messages))
    concentration = [f for f in report.findings
                     if f.detection_method == "collaboration_concentration"]
    assert len(concentration) == 1
    assert len(concentration[0].supporting_evidence_refs) == 3
    assert any("duplicate" in note.lower() for note in report.limitations)


def test_stable_finding_ids_across_runs():
    first = RiskFindingService().analyze_workflow(_entry())
    second = RiskFindingService().analyze_workflow(_entry())
    assert [f.finding_id for f in first.findings] == [f.finding_id for f in second.findings]


def test_hidden_truth_never_enters_findings():
    entry = _entry(messages=[
        {"message_id": "msg-1", "sender": "oracle", "recipient": "chair",
         "round_index": 0, "ground_truth_label": "positive"},
    ])
    report = RiskFindingService().analyze_workflow(entry)
    text = json.dumps(report.model_dump(mode="json"))
    assert "ground_truth" not in text
    assert "oracle_label" not in text


def test_api_mounts_finding_ids_on_recorded_edges(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    entry = _entry("inv-api-risk")
    app.state.warden_workflows.begin("inv-api-risk", {
        "request_id": "inv-api-risk", "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "observation", "scenario_id": "sc-risk",
    })
    app.state.warden_workflows.update(
        "inv-api-risk", "finished", context=entry["context"], result=entry["result"],
    )
    with TestClient(app) as client:
        plain = client.get("/v1/investigations/inv-api-risk/interaction-graph").json()
        enriched = client.get(
            "/v1/investigations/inv-api-risk/interaction-graph",
            params={"include_findings": "true"},
        ).json()
    assert all(edge["finding_ids"] == [] for edge in plain["edges"])
    mounted = [edge for edge in enriched["edges"] if edge["finding_ids"]]
    assert mounted
    assert all(edge["hypothesis_ids"] for edge in mounted)
    assert any("structural signals" in note for note in enriched["limitations"])
