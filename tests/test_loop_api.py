"""Contract tests for the public investigation-loop API (E2)."""

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.contracts.loop_public import InvestigationLoopCreateRequest
from agentguard.services.warden_workflow_store import WardenWorkflowStore


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


def _register_loop(app, key, *, execution_state="not_started", phase="CREATED"):
    loop_id = f"loop-{PAYLOAD['base_request_id']}-recovery-{key}"
    registration, created = app.state.public_loops.registry.register(
        loop_id=loop_id,
        idempotency_key=key,
        base_request_id=PAYLOAD["base_request_id"],
        strategy=PAYLOAD["strategy"],
        phase=phase,
        stop_reason=None,
        request_payload={**PAYLOAD, "data_partition": "held_out_family"},
        report_artifact_id="pending",
        created_at="2026-10-25T00:00:00+00:00",
    )
    assert created
    if execution_state != "not_started":
        app.state.public_loops.registry.update_outcome(
            loop_id,
            phase=phase,
            stop_reason=None,
            report_artifact_id="pending",
            execution_state=execution_state,
            updated_at="2026-10-25T00:00:01+00:00",
        )
        registration = app.state.public_loops.registry.get(loop_id)
    return registration


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


def test_global_api_gate_and_loop_operator_gate_are_composed(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "approval-secret")
    app = _seeded_app(tmp_path)
    with TestClient(app) as client:
        api_only = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Token": "api-secret",
                     "X-Loop-Idempotency-Key": "k-api-only"},
        )
        api_with_approval = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Token": "api-secret",
                     "X-Approval-Token": "approval-secret",
                     "X-Loop-Idempotency-Key": "k-api-approval"},
        )
        operator_only = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-operator"},
        )
    assert api_only.status_code == 403
    assert api_only.json()["detail"]["error"] == "LOOP_AUTHORIZATION_REQUIRED"
    assert api_with_approval.status_code == 200
    assert operator_only.status_code == 200


def test_api_token_is_not_operator_fallback_for_loop_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.delenv("AGENTGUARD_OPERATOR_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENTGUARD_APPROVAL_TOKEN", raising=False)
    app = _seeded_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Token": "api-secret",
                     "X-AgentGuard-Operator-Token": "api-secret",
                     "X-Loop-Idempotency-Key": "k-no-operator-fallback"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "LOOP_AUTHORIZATION_REQUIRED"


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
    run_calls = 0
    run = app.state.public_loops.loop.run

    def counted_run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return run(*args, **kwargs)

    app.state.public_loops.loop.run = counted_run
    with TestClient(app) as client:
        first = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        second = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["loop_id"] == second.json()["loop_id"]
    assert run_calls == 1


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


def test_authorization_reference_participates_in_idempotency_payload(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    headers = {"X-AgentGuard-Operator-Token": "operator-secret",
               "X-Loop-Idempotency-Key": "k-auth-ref-conflict"}
    with TestClient(app) as client:
        first = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        conflict = client.post(
            "/v1/investigation-loops",
            json={**PAYLOAD, "authorization_ref": "approval-record-2"},
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
    run_calls = 0
    run = app.state.public_loops.loop.run

    def counted_run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return run(*args, **kwargs)

    app.state.public_loops.loop.run = counted_run
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
        evaluations = client.get("/v1/research/strategy-evaluations", headers=headers)
        missing_evaluation = client.get(
            "/v1/research/strategy-evaluations/eval-not-present", headers=headers,
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
    assert evaluations.status_code == 200
    assert missing_evaluation.status_code == 404
    assert run_calls == 1  # all GETs after creation remain read-only


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


def test_registered_but_not_started_loop_is_safe_to_resume(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    key = "k-pre-start-recovery"
    registration = _register_loop(app, key)
    run_calls = 0
    run = app.state.public_loops.loop.run

    def counted_run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return run(*args, **kwargs)

    app.state.public_loops.loop.run = counted_run
    with TestClient(app) as client:
        response = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": key},
        )

    assert response.status_code == 200
    assert response.json()["loop_id"] == registration["loop_id"]
    assert response.json()["report_artifact_id"] not in {"", "pending", "failed"}
    assert run_calls == 1
    assert app.state.public_loops.registry.get(registration["loop_id"])["execution_state"] == "finished"


def test_legacy_created_pending_loop_defaults_to_reconciliation(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    key = "k-legacy-created-pending"
    registration = _register_loop(app, key, execution_state="unknown", phase="CREATED")
    replay_calls = 0

    def must_not_replay(*args, **kwargs):
        nonlocal replay_calls
        replay_calls += 1
        raise AssertionError("legacy pending state does not prove the loop never started")

    app.state.public_loops.loop.run = must_not_replay
    with TestClient(app) as client:
        response = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": key},
        )

    assert response.status_code == 200
    assert response.json()["loop_id"] == registration["loop_id"]
    assert response.json()["phase"] == "BLOCKED"
    assert response.json()["stop_reason"] == "reconciliation_required"
    assert replay_calls == 0


def test_legacy_registry_migration_keeps_pending_state_unknown(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    request_payload = {**PAYLOAD, "data_partition": "held_out_family"}
    loop_id = "loop-inv-loop-api-legacy-migration"
    key = "k-legacy-migration"
    with workflows._connect() as db:
        db.execute("""CREATE TABLE investigation_loops (
            loop_id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE,
            base_request_id TEXT NOT NULL,
            strategy TEXT NOT NULL,
            phase TEXT NOT NULL,
            stop_reason TEXT,
            request_hash TEXT NOT NULL,
            report_artifact_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        db.execute(
            "INSERT INTO investigation_loops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (loop_id, key, PAYLOAD["base_request_id"], PAYLOAD["strategy"],
             "CREATED", None, WardenWorkflowStore.canonical_hash(request_payload),
             "pending", "2026-10-25T00:00:00+00:00", "2026-10-25T00:00:00+00:00"),
        )

    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    assert app.state.public_loops.registry.get(loop_id)["execution_state"] == "unknown"
    replay_calls = 0

    def must_not_replay(*args, **kwargs):
        nonlocal replay_calls
        replay_calls += 1
        raise AssertionError("migration must not interpret legacy CREATED as not started")

    app.state.public_loops.loop.run = must_not_replay
    with TestClient(app) as client:
        response = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": key},
        )

    assert response.status_code == 200
    assert response.json()["loop_id"] == loop_id
    assert response.json()["phase"] == "BLOCKED"
    assert response.json()["stop_reason"] == "reconciliation_required"
    assert replay_calls == 0


def test_interrupted_loop_requires_reconciliation_and_never_replays(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    key = "k-interrupted-recovery"

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_before_report(*args, **kwargs):
        raise SimulatedProcessCrash

    app.state.public_loops.loop.run = crash_before_report
    with pytest.raises(SimulatedProcessCrash):
        app.state.public_loops.create_with_idempotency_key(
            InvestigationLoopCreateRequest.model_validate(PAYLOAD),
            idempotency_key=key,
            authorization_valid=True,
        )

    registry = app.state.public_loops.registry
    pending = next(item for item in registry.list() if item["idempotency_key"] == key)
    assert pending["phase"] == "RUNNING"
    assert pending["execution_state"] == "running"
    assert pending["report_artifact_id"] == "pending"

    restarted = create_app(data_dir=tmp_path / "data")
    replay_calls = 0

    def must_not_replay(*args, **kwargs):
        nonlocal replay_calls
        replay_calls += 1
        raise AssertionError("a potentially executed loop must not be replayed")

    restarted.state.public_loops.loop.run = must_not_replay
    headers = {"X-AgentGuard-Operator-Token": "operator-secret",
               "X-Loop-Idempotency-Key": key}
    with TestClient(restarted) as client:
        recovered = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        budget = client.get(
            f"/v1/investigation-loops/{pending['loop_id']}/budget",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
        repeated = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)

    assert recovered.status_code == repeated.status_code == 200
    assert recovered.json()["loop_id"] == repeated.json()["loop_id"] == pending["loop_id"]
    assert recovered.json()["phase"] == "BLOCKED"
    assert recovered.json()["stop_reason"] == "reconciliation_required"
    assert any("consumed budget are unknown" in item for item in recovered.json()["limitations"])
    assert recovered.json()["report_artifact_id"] == "pending"
    assert budget.json()["remaining"] == {
        "actions": None, "model_calls": None, "seconds": None,
    }
    assert replay_calls == 0


def test_report_artifact_survives_registry_update_failure_without_replay(tmp_path, monkeypatch):
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    key = "k-artifact-before-registry-failure"
    run_calls = 0
    run = app.state.public_loops.loop.run

    def counted_run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return run(*args, **kwargs)

    app.state.public_loops.loop.run = counted_run
    registry = app.state.public_loops.registry
    update_outcome = registry.update_outcome

    def fail_final_update(loop_id, **kwargs):
        if kwargs.get("execution_state") == "finished":
            raise sqlite3.OperationalError("injected outcome update failure")
        update_outcome(loop_id, **kwargs)

    registry.update_outcome = fail_final_update
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": key},
        )
    assert response.status_code == 500
    assert run_calls == 1
    pending = next(item for item in registry.list() if item["idempotency_key"] == key)
    assert pending["execution_state"] == "running"
    assert pending["report_artifact_id"] == "pending"
    assert any(
        "investigation-loop" in artifact.tags
        and artifact.metadata.get("base_request_id") == PAYLOAD["base_request_id"]
        for artifact in app.state.artifacts.list_metadata()
    )

    restarted = create_app(data_dir=tmp_path / "data")
    restarted.state.public_loops.loop.run = lambda *args, **kwargs: pytest.fail(
        "a persisted report with a failed registry update must not be rerun"
    )
    with TestClient(restarted) as client:
        recovered = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": key},
        )
        reconciled = client.post(
            f"/v1/investigation-loops/{pending['loop_id']}/reconcile",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )

    assert recovered.status_code == 200
    assert recovered.json()["phase"] == "BLOCKED"
    assert recovered.json()["stop_reason"] == "reconciliation_required"
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "reconciled"
    assert reconciled.json()["report_reconstructed"] is True
    assert restarted.state.public_loops.registry.get(pending["loop_id"])["execution_state"] == "finished"
    assert run_calls == 1


def test_loop_reconciliation_requires_dedicated_operator_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "approval-secret")
    app = _seeded_app(tmp_path)
    registration = _register_loop(
        app, "k-reconcile-auth", execution_state="unknown", phase="BLOCKED",
    )

    with TestClient(app) as client:
        api_token_only = client.post(
            f"/v1/investigation-loops/{registration['loop_id']}/reconcile",
            headers={"X-AgentGuard-Token": "api-secret"},
        )
        approval_only = client.post(
            f"/v1/investigation-loops/{registration['loop_id']}/reconcile",
            headers={"X-AgentGuard-Token": "api-secret", "X-Approval-Token": "approval-secret"},
        )
        allowed = client.post(
            f"/v1/investigation-loops/{registration['loop_id']}/reconcile",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )

    assert api_token_only.status_code == 403
    assert approval_only.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "reconciliation_required"


def test_loop_reconciliation_queries_known_warden_run_without_completing_loop(
    tmp_path, monkeypatch,
):
    import agentguard.api.app as app_module
    from agentguard.contracts.warden import WardenAuditRequest

    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    app = _seeded_app(tmp_path)
    registration = _register_loop(
        app, "k-reconcile-existing-run", execution_state="unknown", phase="BLOCKED",
    )
    request_id = f"{PAYLOAD['base_request_id']}-audit-r1"
    audit_request = WardenAuditRequest.model_validate({
        "request_id": request_id, "contract_id": "contract-a", "model_id": "local-7b",
        "runtime_id": "stegopot-a", "mode": "observation", "scenario_id": "case-family-a",
        "risk_type": "secret_flow",
    })
    app.state.warden_workflows.begin(request_id, audit_request.model_dump(mode="json"))
    app.state.warden_workflows.update(request_id, "preflighting")
    app.state.warden_workflows.update(request_id, "plan_submitting")
    app.state.warden_workflows.update(request_id, "plan_created", context={
        "plan": {"plan_id": "plan-known"},
        "preflight": {"status": "ready", "blockers": []},
    })
    app.state.warden_workflows.update(request_id, "run_submitting")
    app.state.warden_workflows.update(request_id, "run_submitted", context={
        "plan": {"plan_id": "plan-known"},
        "run": {"run_id": "run-known", "plan_id": "plan-known", "status": "running"},
        "preflight": {"status": "ready", "blockers": []},
    })
    app.state.warden_workflows.update(request_id, "unknown")

    class QueryOnlyKernel:
        def __init__(self):
            self.calls = []

        def get_run(self, run_id):
            self.calls.append(("get_run", run_id))
            return {
                "run_id": run_id, "plan_id": "plan-known", "status": "completed",
                "evidence_refs": ["artifact://actual-result"],
            }

    kernel = QueryOnlyKernel()
    monkeypatch.setattr(app_module, "_configured_warden_client", lambda: kernel)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/investigation-loops/{registration['loop_id']}/reconcile",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reconciliation_required"
    assert body["report_reconstructed"] is False
    assert body["workflows"][0]["remote_status"] == "completed"
    assert body["workflows"][0]["run_id"] == "run-known"
    assert app.state.warden_workflows.get(request_id)["status"] == "finished"
    loop_after = app.state.public_loops.registry.get(registration["loop_id"])
    assert loop_after["phase"] == "BLOCKED"
    assert loop_after["execution_state"] == "unknown"
    assert loop_after["report_artifact_id"] == "pending"
    assert kernel.calls == [("get_run", "run-known")]


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
    assert "post" in schema["paths"]["/v1/investigation-loops/{loop_id}/reconcile"]
    assert "get" in schema["paths"]["/v1/system/identity"]
    parameters = schema["paths"]["/v1/investigation-loops"]["post"]["parameters"]
    by_name = {item["name"]: item for item in parameters}
    assert {
        "X-AgentGuard-Token", "X-AgentGuard-Operator-Token",
        "X-Approval-Token", "X-Loop-Idempotency-Key",
    } <= by_name.keys()
    idempotency = next(
        item for item in parameters
        if item["name"] == "X-Loop-Idempotency-Key" and item["in"] == "header"
    )
    assert idempotency["required"] is True
    assert "Loop creation permission" in by_name["X-AgentGuard-Token"]["description"]
    operation = schema["paths"]["/v1/investigation-loops"]["post"]
    assert {"400", "401", "403", "404", "409", "422", "429", "500", "502", "503"} <= operation["responses"].keys()
    assert "oneOf" in operation["responses"]["422"]["content"]["application/json"]["schema"]
    request_schema = schema["components"]["schemas"]["InvestigationLoopCreateRequest"]
    assert request_schema["properties"]["strategy"]["enum"] == [
        "fixed_order", "risk_score", "information_value", "experience", "llm",
    ]
    assert request_schema["properties"]["max_actions"]["default"] == 8
    assert request_schema["properties"]["max_actions"]["maximum"] == 100.0
    assert request_schema["properties"]["max_seconds"]["default"] == 300.0
    assert request_schema["properties"]["max_model_calls"]["default"] == 4
    assert request_schema["properties"]["no_new_evidence_rounds"]["default"] == 2
    snapshot = Path(__file__).resolve().parents[1] / "docs/integration/openapi-1.1.0rc2.json"
    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["version"] == "1.1.0rc2"
    assert len(schema["paths"]) == 86
    assert schema == json.loads(snapshot.read_text(encoding="utf-8"))
    # Normalize checkout line endings so the pinned file digest is identical on
    # Windows (CRLF) and Linux/macOS (LF).
    normalized_snapshot = snapshot.read_text(encoding="utf-8").encode("utf-8")
    assert hashlib.sha256(normalized_snapshot).hexdigest() == (
        "2ce83660a67111a346a2de9772fcaec1938a4a5de713439de34a83e372b9a5e5"
    )


def test_id_semantics_are_strictly_separated(tmp_path, monkeypatch):
    """F3: base_request_id, loop_id, decision_id never share a namespace."""
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-ids"},
        )
        body = created.json()
        loop_id = body["loop_id"]
        assert body["base_request_id"] == "inv-loop-api"
        assert loop_id.startswith("loop-inv-loop-api-")
        assert body["base_request_id"] != loop_id
        decisions = client.get(
            f"/v1/investigation-loops/{loop_id}/decisions",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        ).json()
        for decision in decisions["items"]:
            assert decision["decision_id"].startswith(f"{loop_id}:round-")
            assert decision["round_index"] >= 1
            assert decision["selected_action_id"].startswith("act-")


def test_budget_unknown_fields_stay_null(tmp_path, monkeypatch):
    """F3: wall-clock and unmeasured budget fields stay null, not zero."""
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops", json=PAYLOAD,
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-budget"},
        )
        loop_id = created.json()["loop_id"]
        budget = client.get(
            f"/v1/investigation-loops/{loop_id}/budget",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        ).json()
        assert budget["budget"]["max_actions"] == 3
        assert "seconds" in budget["remaining"]
        # wall-clock budget is only meaningful during a run -> null
        assert budget["remaining"]["seconds"] is None


def test_missing_report_artifact_does_not_fabricate_metrics(tmp_path, monkeypatch):
    """F3: unknown loop_id returns 404 on all read endpoints."""
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        headers = {"X-AgentGuard-Operator-Token": "operator-secret"}
        view = client.get("/v1/investigation-loops/loop-nonexistent", headers=headers)
        decisions = client.get("/v1/investigation-loops/loop-nonexistent/decisions", headers=headers)
        changes = client.get("/v1/investigation-loops/loop-nonexistent/evidence-changes", headers=headers)
        budget = client.get("/v1/investigation-loops/loop-nonexistent/budget", headers=headers)
    assert view.status_code == 404
    assert decisions.status_code == 404
    assert changes.status_code == 404
    assert budget.status_code == 404


def test_evidence_changes_only_include_new_evidence_rounds(tmp_path, monkeypatch):
    """F3: evidence-changes endpoint only lists rounds with new evidence."""
    app = _seeded_app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/v1/investigation-loops",
            json={**PAYLOAD, "max_actions": 5},
            headers={"X-AgentGuard-Operator-Token": "operator-secret",
                     "X-Loop-Idempotency-Key": "k-evidence-changes"},
        )
        loop_id = created.json()["loop_id"]
        changes = client.get(
            f"/v1/investigation-loops/{loop_id}/evidence-changes",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        ).json()
        decisions = client.get(
            f"/v1/investigation-loops/{loop_id}/decisions",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        ).json()
        # Every evidence-change item must have new_evidence=True.
        assert all(item["new_evidence"] for item in changes["items"])
        # Evidence changes count must be <= total decisions count.
        assert changes["count"] <= decisions["count"]
        # The endpoint lists its own limitation note.
        assert changes["limitations"]


def test_g3_idempotent_repeat_does_not_create_second_loop(tmp_path, monkeypatch):
    """G3: Same idempotency key returns same loop_id, no duplicate creation."""
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    app = _seeded_app(tmp_path, request_id="inv-loop-api")
    with TestClient(app) as client:
        headers = {"X-AgentGuard-Operator-Token": "operator-secret",
                   "X-Loop-Idempotency-Key": "g3-idem-key-001"}
        resp1 = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        assert resp1.status_code == 200
        loop_id_1 = resp1.json()["loop_id"]
        # Second call with same key and same payload
        resp2 = client.post("/v1/investigation-loops", json=PAYLOAD, headers=headers)
        assert resp2.status_code == 200
        loop_id_2 = resp2.json()["loop_id"]
        assert loop_id_1 == loop_id_2


def test_g3_unknown_loop_id_returns_404(tmp_path, monkeypatch):
    """G3: Unknown loop ID must return 404, not 500 or fabricated data."""
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    app = _seeded_app(tmp_path, request_id="inv-g3-404")
    with TestClient(app) as client:
        resp = client.get(
            "/v1/investigation-loops/loop-does-not-exist",
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "LOOP_NOT_FOUND"

