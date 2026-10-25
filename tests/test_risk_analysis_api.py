"""Contract tests for the public risk-analysis API (E1)."""

import json

from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def _entry(request_id="inv-risk-api", messages=None, findings=None):
    messages = messages if messages is not None else [
        {"message_id": "msg-1", "sender": "rev_a", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-2", "sender": "rev_b", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-3", "sender": "rev_c", "recipient": "chair", "round_index": 0},
    ]
    run = {
        "run_id": f"run-{request_id}", "plan_id": f"plan-{request_id}",
        "status": "completed",
        "evidence": {
            "findings": [{"kind": "observational_run", "messages": messages}],
            "artifacts": [{"artifact_id": "a" * 64,
                           "uri": "file:///D:/never/opened.json"}],
        },
    }
    return {
        "request_id": request_id, "status": "finished", "workflow_status": "completed",
        "request": {"scenario_id": "sc-risk", "model_id": "model-7b"},
        "context": {"run": run, "plan": {"plan_id": run["plan_id"]}},
        "result": {"status": "completed", "run": run},
        "run_id": run["run_id"],
    }


def _seeded_app(tmp_path, entry):
    app = create_app(data_dir=tmp_path / "data")
    app.state.warden_workflows.begin(entry["request_id"], {
        "request_id": entry["request_id"], "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "observation", "scenario_id": "sc-risk",
    })
    app.state.warden_workflows.update(
        entry["request_id"], "finished", context=entry["context"],
        result=entry["result"],
    )
    return app


def test_real_warden_metadata_yields_findings_hypotheses_and_provenance(tmp_path):
    app = _seeded_app(tmp_path, _entry())
    with TestClient(app) as client:
        response = client.get("/v1/investigations/inv-risk-api/risk-analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["available"] is True
    assert body["data_source"] == "warden_observation"
    assert body["evidence_level"] == "observational"
    assert body["findings"] and body["hypotheses"]
    finding = body["findings"][0]
    assert {"finding_id", "risk_type", "detection_method", "status", "severity",
            "score", "score_calibration_status", "supporting_evidence_refs",
            "graph_node_refs", "graph_edge_refs", "limitations"} <= set(finding)
    assert finding["score"] is None
    assert finding["score_calibration_status"] == "uncalibrated"
    assert finding["verification_status"] == "unverified"
    assert finding["graph_edge_refs"] and finding["graph_node_refs"]
    hypothesis = body["hypotheses"][0]
    assert {"hypothesis_id", "finding_id", "statement", "expected_effect",
            "supporting_evidence", "contradicting_evidence", "next_check",
            "required_capabilities", "intervention_supported"} <= set(hypothesis)
    assert hypothesis["intervention_supported"] is False


def test_normal_two_party_communication_yields_no_candidate(tmp_path):
    app = _seeded_app(tmp_path, _entry(messages=[
        {"message_id": "msg-1", "sender": "a", "recipient": "b", "round_index": 0},
        {"message_id": "msg-2", "sender": "c", "recipient": "b", "round_index": 0},
    ]))
    with TestClient(app) as client:
        body = client.get("/v1/investigations/inv-risk-api/risk-analysis").json()
    candidates = [f for f in body["findings"] if f["status"] == "candidate"]
    assert candidates == []
    assert body["hypotheses"] == []


def test_missing_evidence_reports_unavailable_not_fabrication(tmp_path):
    entry = _entry()
    entry["context"]["run"]["evidence"]["findings"] = []
    app = _seeded_app(tmp_path, entry)
    with TestClient(app) as client:
        response = client.get("/v1/investigations/inv-risk-api/risk-analysis")
        missing = client.get("/v1/investigations/inv-none/risk-analysis")
    body = response.json()
    assert body["available"] is False
    assert body["data_source"] == "none"
    assert body["evidence_level"] == "unknown"
    assert body["findings"] == [] and body["hypotheses"] == []
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "WORKFLOW_NOT_FOUND"


def test_graph_only_fallback_via_run_id_is_labelled_synthetic(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    with TestClient(app) as client:
        report = client.post("/v1/runs", json={"scenario_id": scenario}).json()
        body = client.get(
            "/v1/investigations/inv-synth/risk-analysis",
            params={"run_id": report["run_id"]},
        ).json()
    if body["available"]:
        assert body["evidence_level"] == "synthetic"
        assert all(f["evidence_level"] == "synthetic" for f in body["findings"])


def test_no_message_bodies_or_paths_in_response(tmp_path):
    app = _seeded_app(tmp_path, _entry())
    with TestClient(app) as client:
        body = client.get("/v1/investigations/inv-risk-api/risk-analysis").json()
    text = json.dumps(body)
    assert "file:" not in text
    assert "ground_truth" not in text and "oracle" not in text
    assert "private_directive" not in text
    # Metadata fields (sender/recipient) are structural anchors, never prose:
    assert all("private" not in f["description"].lower() for f in body["findings"])


def test_duplicate_messages_are_collapsed_not_double_counted(tmp_path):
    messages = [
        {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-3", "sender": "c", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 0},
    ]
    app = _seeded_app(tmp_path, _entry(messages=messages))
    with TestClient(app) as client:
        body = client.get("/v1/investigations/inv-risk-api/risk-analysis").json()
    concentration = [f for f in body["findings"]
                     if f["detection_method"] == "collaboration_concentration"]
    assert len(concentration) == 1
    assert len(concentration[0]["supporting_evidence_refs"]) == 3


def test_unresolvable_graph_refs_do_not_crash(tmp_path):
    # A finding whose target is absent from the graph must still serialize.
    messages = [
        {"message_id": "m" * 300, "sender": "a", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-3", "sender": "c", "recipient": "chair", "round_index": 0},
    ]
    app = _seeded_app(tmp_path, _entry(messages=messages))
    with TestClient(app) as client:
        response = client.get("/v1/investigations/inv-risk-api/risk-analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True


def test_cross_request_isolation(tmp_path):
    app = _seeded_app(tmp_path, _entry("inv-iso-a"))
    other = _entry("inv-iso-b", messages=[
        {"message_id": "x-1", "sender": "solo", "recipient": "chair", "round_index": 0},
    ])
    app.state.warden_workflows.begin("inv-iso-b", {
        "request_id": "inv-iso-b", "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "observation", "scenario_id": "sc-risk",
    })
    app.state.warden_workflows.update("inv-iso-b", "finished",
                                      context=other["context"], result=other["result"])
    with TestClient(app) as client:
        a = client.get("/v1/investigations/inv-iso-a/risk-analysis").json()
        b = client.get("/v1/investigations/inv-iso-b/risk-analysis").json()
    assert a["investigation_id"] == "inv-iso-a"
    assert b["investigation_id"] == "inv-iso-b"
    a_ids = {f["finding_id"] for f in a["findings"]}
    b_ids = {f["finding_id"] for f in b["findings"]}
    assert a_ids != b_ids


def test_hidden_truth_injection_stays_out(tmp_path):
    messages = [
        {"message_id": "msg-1", "sender": "oracle", "recipient": "chair",
         "round_index": 0, "ground_truth_label": "positive"},
        {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 0},
        {"message_id": "msg-3", "sender": "c", "recipient": "chair", "round_index": 0},
    ]
    app = _seeded_app(tmp_path, _entry(messages=messages))
    with TestClient(app) as client:
        body = client.get("/v1/investigations/inv-risk-api/risk-analysis").json()
    assert "ground_truth" not in json.dumps(body)


def test_error_responses_are_sanitized(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        missing = client.get("/v1/investigations/inv-x/risk-analysis")
        bad_run = client.get(
            "/v1/investigations/inv-x/risk-analysis", params={"run_id": "run-x"},
        )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "WORKFLOW_NOT_FOUND"
    assert bad_run.status_code == 404
    assert bad_run.json()["detail"]["error"] == "RUN_ID_NOT_FOUND"


def test_api_requires_token_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    app = _seeded_app(tmp_path, _entry())
    with TestClient(app) as client:
        denied = client.get("/v1/investigations/inv-risk-api/risk-analysis")
        allowed = client.get(
            "/v1/investigations/inv-risk-api/risk-analysis",
            headers={"X-AgentGuard-Token": "api-secret"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.headers.get("X-Request-ID")


def test_openapi_registers_risk_analysis_route(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        schema = client.get("/openapi.json").json()
    route = schema["paths"].get("/v1/investigations/{request_id}/risk-analysis")
    assert route is not None and "get" in route
