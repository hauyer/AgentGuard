"""Read-only contract compatibility tests against the QWB application layer (C7).

These tests pin the response structures the AgentGuard-QWB-App BFF and its
TypeScript types depend on (contracts/agentguard-version.json @ 7d42809) plus
the additive C1-C6 surface. The app repository stays read-only; this file
documents and enforces the integration boundary from the AgentGuard side.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.api.app import create_app

BASELINE = Path(__file__).resolve().parents[1] / "docs" / "integration" / "openapi-c0-baseline.json"


def _app(tmp_path):
    return create_app(data_dir=tmp_path / "data")


def test_openapi_changes_since_c0_are_additive_only():
    with TestClient(_app_path_tmp()) as client:
        current = client.get("/openapi.json").json()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    for path, methods in baseline["paths"].items():
        assert path in current["paths"], f"baseline path removed: {path}"
        for method in methods:
            assert method in current["paths"][path], (
                f"baseline method removed: {method.upper()} {path}"
            )
    new_paths = sorted(set(current["paths"]) - set(baseline["paths"]))
    assert new_paths == [
        "/v1/experiments",
        "/v1/experiments/compare",
        "/v1/experiments/{experiment_id}",
        "/v1/investigations/options",
        "/v1/investigations/{request_id}/interaction-graph",
        "/v1/investigations/{request_id}/timeline",
        "/v1/reports",
        "/v1/system/readiness",
    ]


def _app_path_tmp():
    import tempfile

    return create_app(data_dir=Path(tempfile.mkdtemp()) / "data")


def test_dashboard_status_contract_shapes(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        health = client.get("/v1/system/health").json()
        capabilities = client.get("/v1/system/capabilities").json()
        kernel = client.get("/v1/kernel/status").json()
    assert health["status"] == "ok"
    assert health["service"] == "agentguard"
    assert health["version"]
    assert capabilities["backend_mode"] in {"synthetic", "real"}
    assert isinstance(capabilities["capabilities"], dict)
    assert isinstance(capabilities["supported_target_types"], list)
    assert isinstance(capabilities["limitations"], list)
    assert kernel["kernel"] == "WARDEN"
    assert kernel["status"] in {"available", "degraded", "unreachable", "not_configured", "misconfigured"}
    assert kernel["authoritative_for"]


def test_investigation_list_and_detail_contract(tmp_path):
    app = _app(tmp_path)
    app.state.warden_workflows.begin("inv-compat", {
        "request_id": "inv-compat", "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "observation", "scenario_id": "sc",
    })
    with TestClient(app) as client:
        listed = client.get("/v1/investigations").json()
        assert {"items", "count", "offset", "limit", "next_offset"} <= set(listed)
        entry = next(item for item in listed["items"] if item["request_id"] == "inv-compat")
        assert {"request_id", "status", "workflow_status", "revision",
                "created_at", "updated_at"} <= set(entry)
        detail = client.get("/v1/investigations/inv-compat").json()
        assert {"request_id", "status", "workflow_status", "revision", "statistics",
                "created_at", "updated_at", "plan_id", "run_id", "result",
                "error_code", "error_message"} <= set(detail)
        missing = client.get("/v1/investigations/inv-none")
        assert missing.status_code == 404
        assert missing.json()["detail"]["error"] == "WORKFLOW_NOT_FOUND"


def test_investigation_submission_error_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/v1/investigations", json={
            "request_id": "no-kernel", "contract_id": "c", "model_id": "m",
            "runtime_id": "r", "mode": "observation", "start_run": False,
        })
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "WARDEN_NOT_CONFIGURED"


def test_evidence_contract_fields_for_app(tmp_path):
    app = _app(tmp_path)
    app.state.warden_workflows.begin("inv-ev", {
        "request_id": "inv-ev", "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "observation", "scenario_id": "sc",
        "risk_type": "secret_flow",
    })
    with TestClient(app) as client:
        evidence = client.get("/v1/evidence/investigations/inv-ev").json()
    assert {"request_id", "workflow_status", "run_status", "items", "limitations"} <= set(evidence)


def test_defense_contract_fields_for_app(tmp_path):
    app = _app(tmp_path)
    store = app.state.warden_workflows
    store.begin("inv-def", {
        "request_id": "inv-def", "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "causal_audit", "scenario_id": "sc",
        "risk_type": "secret_flow",
    })
    run = {
        "run_id": "run-def", "plan_id": "plan-def", "status": "completed",
        "evidence_bundle": {"items": [{
            "evidence_id": "ev-def", "type": "finding", "risk_type": "secret_flow",
            "target_type": "message", "target_ref": "message-17",
        }]},
    }
    store.update("inv-def", "finished", context={"run": run},
                 result={"status": "completed", "run": run})
    with TestClient(app) as client:
        preview = client.post("/v1/defense-policies/inv-def/preview").json()
    assert {"schema_version", "run_id", "status", "candidates", "policy_notes",
            "execution_performed", "evidence_mode"} <= set(preview)
    assert {"approval_required_per_candidate", "execution_supported_overall",
            "rollback_supported", "verification_evidence_available",
            "benign_utility_data_available", "capability_notes"} <= set(preview)


def test_report_read_contract_for_app(tmp_path):
    app = _app(tmp_path)
    scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    with TestClient(app) as client:
        created = client.post("/v1/runs", json={
            "scenario_id": scenario, "experiment_id": "exp-compat",
        }).json()
        report = client.get(f"/v1/reports/{created['run_id']}").json()
        index = client.get("/v1/reports").json()
    assert report["run_id"] == created["run_id"]
    assert report["findings"] is not None and report["limitations"] is not None
    assert index["total"] >= 1


def test_experiences_contract_for_app(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        payload = client.get("/v1/experiences", params={"verified_only": "true"}).json()
    assert {"query", "items", "total"} <= set(payload)


def test_422_validation_errors_do_not_echo_input(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/v1/experiments/compare", json={"run_ids": ["only-one"]})
    assert response.status_code == 422
    body = response.json()
    assert "only-one" not in json.dumps(body)
    assert all(set(error) == {"type", "loc", "msg"} for error in body["detail"])


def test_untrusted_token_cannot_reach_operator_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    with TestClient(_app(tmp_path)) as client:
        denied = client.get(
            "/v1/system/audit-events", headers={"X-AgentGuard-Token": "api-secret"}
        )
        allowed = client.get(
            "/v1/system/audit-events",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    # The API token cannot be used as a role claim to elevate itself.
    with TestClient(_app(tmp_path)) as client:
        forged = client.get(
            "/v1/system/audit-events",
            headers={"X-AgentGuard-Token": "api-secret", "X-Role": "admin"},
        )
    assert forged.status_code in {401, 403}


def test_audit_events_do_not_leak_credentials_or_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    with TestClient(_app(tmp_path)) as client:
        client.get("/v1/investigations?status=completed",
                   headers={"X-AgentGuard-Operator-Token": "operator-secret"})
        events = client.get(
            "/v1/system/audit-events",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        ).json()
    assert "api-secret" not in json.dumps(events)
    assert "status=completed" not in json.dumps(events)
    investigation_event = next(
        item for item in events["items"] if item["path"] == "/v1/investigations"
    )
    assert investigation_event["actor_role"] == "operator"
    assert investigation_event["completed"] is True
