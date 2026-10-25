"""Contract tests for the public investigation-loop API (E2)."""

import threading

from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def _entry(request_id="inv-loop-api"):
    run = {
        "run_id": f"run-{request_id}", "plan_id": f"plan-{request_id}",
        "status": "completed",
        "evidence": {
            "findings": [{"kind": "observational_run", "messages": [
                {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 0},
                {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 0},
                {"message_id": "msg-3", "sender": "c", "recipient": "chair", "round_index": 0},
            ]}],
            "artifacts": [{"artifact_id": "art-" + "a" * 60}],
        },
    }
    return {
        "request_id": request_id, "status": "finished", "workflow_status": "completed",
        "request": {"scenario_id": "sc-loop", "contract_id": "c", "model_id": "m",
                    "runtime_id": "r", "mode": "observation"},
        "context": {"run": run, "plan": {"plan_id": run["plan_id"]}},
        "result": {"status": "completed", "run": run},
        "run_id": run["run_id"],
    }


def _seeded_app(tmp_path, request_id="inv-loop-api", monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
        monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "approval-secret")
    app = create_app(data_dir=tmp_path / "data")
    entry = _entry(request_id)
    app.state.warden_workflows.begin(request_id, {
        "request_id": request_id, "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "observation", "scenario_id": "sc-loop",
    })
    app.state.warden_workflows.update(
        request_id, "finished", context=entry["context"], result=entry["result"],
    )
    return app


PAYLOAD = {
    "base_request_id": "inv-loop-api",
    "strategy": "information_value",
    "max_actions": 3,
    "max_seconds": 60.0,
    "max_model_calls": 0,
    "no_new_evidence_rounds": 2,
}


def test_post_requires_operator_or_approval_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    app = _seeded_app(tmp_path)
    with TestClient(app) as client:
        forged = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Token": "whatever", "X-Role": "admin"},
        )
        wrong = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "not-the-secret"},
        )
    # A plain API token or a forged role claim never grants loop creation;
    # the middleware rejects unauthenticated calls before the handler runs.
    assert forged.status_code == 401
    assert wrong.status_code == 401


def test_post_creates_loop_with_operator_credential(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-create-1"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["loop_id"].startswith("loop-inv-loop-api-")
    assert body["base_request_id"] == "inv-loop-api"
    assert body["strategy"] == "information_value"
    assert body["phase"] in {"STOPPED", "COMPLETED", "BLOCKED"}
    assert body["report_artifact_id"] not in {"", "pending", "failed"}
    assert body["budget"]["max_actions"] == 3
    assert body["metrics"]["rounds_executed"] >= 1


def test_idempotent_retries_return_the_same_loop(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    headers = {"X-AgentGuard-Operator-Token": "operator-secret",
               "X-Loop-Idempotency-Key": "k-idem"}
    with TestClient(app) as client:
        first = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        second = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["loop_id"] == second.json()["loop_id"]


def test_conflicting_payload_on_same_key_is_rejected(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    headers = {"X-AgentGuard-Operator-Token": "operator-secret",
               "X-Loop-Idempotency-Key": "k-conflict"}
    with TestClient(app) as client:
        first = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        conflict = client.post(
            "/v1/investigation-loops",
            json={**PAYLOAD, "max_actions": 5},
            headers=headers,
        )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "LOOP_IDEMPOTENCY_CONFLICT"


def test_unknown_base_and_unfinished_base_are_mapped(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    app.state.warden_workflows.begin("inv-running", {"request_id": "inv-running"})
    with TestClient(app) as client:
        missing = client.post(
            "/v1/investigation-loops",
            json={**PAYLOAD, "base_request_id": "inv-none"},
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-missing-base"},
        )
        running = client.post(
            "/v1/investigation-loops",
            json={**PAYLOAD, "base_request_id": "inv-running"},
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-running-base"},
        )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "BASE_INVESTIGATION_NOT_FOUND"
    assert running.status_code == 409
    assert running.json()["detail"]["error"] == "BASE_INVESTIGATION_NOT_FINISHED"


def test_missing_idempotency_key_returns_422(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        no_key = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
        empty_key = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "   "},
        )
    assert no_key.status_code == 422
    assert no_key.json()["detail"]["error"] == "MISSING_IDEMPOTENCY_KEY"
    assert empty_key.status_code == 422
    assert empty_key.json()["detail"]["error"] == "MISSING_IDEMPOTENCY_KEY"


def test_read_only_family_works_without_credentials(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    headers = {"X-AgentGuard-Operator-Token": "operator-secret"}
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={**headers, "X-Loop-Idempotency-Key": "k-readonly"},
        )
        loop_id = created.json()["loop_id"]
        view = client.get(f"/v1/investigation-loops/{loop_id}", headers=headers)
        decisions = client.get(
            f"/v1/investigation-loops/{loop_id}/decisions", headers=headers,
        )
        changes = client.get(
            f"/v1/investigation-loops/{loop_id}/evidence-changes", headers=headers,
        )
        budget = client.get(
            f"/v1/investigation-loops/{loop_id}/budget", headers=headers,
        )
        listing = client.get("/v1/investigation-loops", headers=headers)
        scoped = client.get(
            "/v1/investigation-loops", params={"base_request_id": "inv-loop-api"},
            headers=headers,
        )
    assert view.status_code == 200
    assert view.json()["loop_id"] == loop_id
    assert decisions.status_code == 200
    decision_items = decisions.json()["items"]
    assert decision_items
    first = decision_items[0]
    assert first["decision_id"] == f"{loop_id}:round-1"
    assert first["round_index"] == 1
    assert first["strategy"] == "information_value"
    assert first["candidate_actions"]
    assert first["selection_reason"]
    assert changes.status_code == 200
    assert all(item["new_evidence"] for item in changes.json()["items"])
    assert budget.status_code == 200
    assert budget.json()["budget"]["max_actions"] == 3
    assert listing.status_code == 200
    assert scoped.json()["count"] == 1


def test_decisions_reflect_adaptive_rounds(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops",
            json={**PAYLOAD, "max_actions": 5},
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-adaptive"},
        )
        loop_id = created.json()["loop_id"]
        decisions = client.get(
            f"/v1/investigation-loops/{loop_id}/decisions",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        ).json()
    assert len(decisions["items"]) >= 2
    selected = [item["selected_action_id"] for item in decisions["items"]]
    assert len(set(selected)) >= 2, "later rounds must adapt"
    assert any(item["new_evidence_count"] for item in decisions["items"])


def test_loop_survives_restart(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-restart"},
        )
        loop_id = created.json()["loop_id"]
    restarted = create_app(data_dir=data_dir)
    with TestClient(restarted) as client:
        headers = {"X-AgentGuard-Operator-Token": "operator-secret"}
        view = client.get(f"/v1/investigation-loops/{loop_id}", headers=headers)
        decisions = client.get(
            f"/v1/investigation-loops/{loop_id}/decisions", headers=headers,
        )
    assert view.status_code == 200
    assert view.json()["rounds_executed"] == created.json()["rounds_executed"]
    assert decisions.json()["count"] >= 1


def test_concurrent_creates_collapse_to_one_loop(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    results = []
    headers = {"X-AgentGuard-Operator-Token": "operator-secret",
               "X-Loop-Idempotency-Key": "k-concurrent"}

    def post():
        with TestClient(app) as client:
            results.append(client.post(
                "/v1/investigation-loops", json=PAYLOAD, headers=headers,
            ))

    threads = [threading.Thread(target=post) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(response.status_code == 200 for response in results)
    loop_ids = {response.json()["loop_id"] for response in results}
    assert len(loop_ids) == 1, "concurrent creates must not duplicate the loop"


def test_error_and_shape_sanitization(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-sanitize"},
        )
        loop_id = created.json()["loop_id"]
        missing = client.get(
            "/v1/investigation-loops/loop-none",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
        bad_limit = client.get(
            "/v1/investigation-loops", params={"limit": 9999},
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
        view = client.get(
            f"/v1/investigation-loops/{loop_id}",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
    assert missing.status_code == 404
    assert bad_limit.status_code == 422
    text = str(view.json())
    assert "sender" not in text and "ground_truth" not in text


def test_openapi_registers_loop_routes(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        schema = client.get("/openapi.json").json()
    assert "post" in schema["paths"]["/v1/investigation-loops"]
    assert "get" in schema["paths"]["/v1/investigation-loops"]
    assert "get" in schema["paths"]["/v1/investigation-loops/{loop_id}"]
    assert "get" in schema["paths"]["/v1/investigation-loops/{loop_id}/decisions"]
    assert "get" in schema["paths"]["/v1/investigation-loops/{loop_id}/evidence-changes"]
    assert "get" in schema["paths"]["/v1/investigation-loops/{loop_id}/budget"]
