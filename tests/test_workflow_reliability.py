"""Concurrency, process exit, and terminal-state guarantees for the existing journal."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient
from agentguard.api.app import create_app
from agentguard.cli import main
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.services.warden_lifecycle import WardenLifecycleService
from agentguard.services.warden_workflow_store import WardenWorkflowStore, WorkflowConflictError
from agentguard.services.workflow_state import (
    WorkflowState,
    WorkflowTransitionError,
    kernel_to_workflow_state,
    project_workflow_state,
    workflow_to_kernel_status,
)


def _request():
    return WardenAuditRequest(request_id="request-1", contract_id="contract-1",
                              model_id="model-1", runtime_id="runtime-1", mode="observation")


def test_concurrent_same_request_has_one_external_submission(tmp_path):
    started, release = Event(), Event()
    calls = []

    class Kernel:
        def health(self):
            return {"status": "ok", "version": "0.2.0", "api_version": "v1"}

        def capabilities(self):
            return []

        def contracts(self):
            return ["contract-1"]

        def create_audit_plan(self, payload):
            calls.append("plan")
            return {"plan_id": "plan-1", "status": "planned"}

        def start_run(self, plan_id):
            calls.append("run")
            started.set()
            assert release.wait(5)
            return {"run_id": "run-1", "plan_id": plan_id, "status": "completed"}

    path = tmp_path / "workflows.sqlite3"
    first = WardenLifecycleService(Kernel(), workflows=WardenWorkflowStore(path))
    second = WardenLifecycleService(Kernel(), workflows=WardenWorkflowStore(path))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.execute, _request())
        try:
            assert started.wait(5)
            assert second.execute(_request())["status"] == "in_progress"
            with pytest.raises(WorkflowConflictError):
                second.execute(_request().model_copy(update={"model_id": "different"}))
        finally:
            release.set()
        assert future.result(timeout=5)["workflow_status"] == "completed"
    assert second.execute(_request())["idempotent_replay"] is True
    assert calls == ["plan", "run"]
    stats = WardenWorkflowStore(path).execution_statistics("request-1")
    assert stats["processing_attempts"] == 1
    assert stats["retry_count"] == 0
    assert stats["plan_submission_attempts"] == stats["run_submission_attempts"] == 1


def test_process_exit_releases_execution_lock_without_erasing_journal(tmp_path):
    path = tmp_path / "workflows.sqlite3"
    store = WardenWorkflowStore(path)
    store.begin("request-1", _request().model_dump(mode="json"))
    code = (
        "import sys; from agentguard.services.warden_workflow_store import WardenWorkflowStore; "
        "store=WardenWorkflowStore(sys.argv[1]); "
        "lock=store.execution_lock('request-1'); "
        "print(lock.__enter__(),flush=True); sys.stdin.read()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert process.stdout.readline().strip() == "True"
        with store.execution_lock("request-1") as acquired:
            assert acquired is False
    finally:
        process.terminate()
        process.communicate(timeout=5)
    with store.execution_lock("request-1") as acquired:
        assert acquired is True
    assert store.get("request-1")["status"] == "created"


def test_terminal_results_are_immutable_and_connections_close(tmp_path):
    path = tmp_path / "workflows.sqlite3"
    store = WardenWorkflowStore(path)
    store.begin("request-1", {"request_id": "request-1"})
    store.update("request-1", "finished", result={"status": "completed"})
    before = store.get("request-1")
    with pytest.raises(WorkflowTransitionError):
        store.update("request-1", "created")
    with pytest.raises(WorkflowTransitionError):
        store.update("request-1", "finished", result={"status": "failed"})
    assert store.get("request-1") == before
    assert len(store.events("request-1")) == 2
    # Windows refuses this unlink if any journal operation leaks a DB handle.
    path.unlink()
    assert not path.exists()


def test_cancel_only_unsubmitted_local_work_and_preserve_external_references(tmp_path):
    app = create_app(data_dir=tmp_path)
    store = app.state.warden_workflows
    store.begin("local", {"request_id": "local"})
    store.begin("external", {"request_id": "external"})
    store.update("external", "run_submitted", context={
        "plan": {"plan_id": "plan-1"},
        "run": {"run_id": "run-1", "status": "queued"},
    })
    with TestClient(app) as client:
        assert client.post("/v1/kernel/workflows/local/cancel").json()["status"] == "cancelled"
        result = client.post("/v1/kernel/workflows/external/cancel").json()
        assert result["status"] == "unsupported"
        assert store.get("external")["run_id"] == "run-1"
        assert store.get("external")["workflow_status"] == "queued"
        assert client.post("/v1/kernel/workflows/missing/cancel").status_code == 404


def test_business_states_do_not_confuse_local_completion_with_model_completion():
    assert project_workflow_state("finished", {}, {"status": "planned"}) is WorkflowState.PLANNED
    assert project_workflow_state("run_submitted", {
        "run": {"status": "queued"},
    }, None) is WorkflowState.QUEUED
    assert kernel_to_workflow_state("new-remote-status") is WorkflowState.UNKNOWN
    assert workflow_to_kernel_status(WorkflowState.VALIDATING) is None
    assert workflow_to_kernel_status(WorkflowState.FAILED) == "failed"


def _kernel_client():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url))
        path = url.removeprefix("https://warden.example")
        responses = {
            "/v1/health": {"status": "ok", "version": "0.2.0", "api_version": "v1"},
            "/v1/capabilities": [], "/v1/contracts": ["contract-1"],
            "/v1/audit-plans": {"plan_id": "plan-1", "status": "planned"},
            "/v1/runs": {"run_id": "run-1", "plan_id": "plan-1", "status": "completed"},
        }
        return 200, json.dumps(responses[path]).encode()

    return WardenKernelClient("https://warden.example", transport=transport), calls


def test_audit_compatibility_alias_shares_durable_idempotency(tmp_path, monkeypatch):
    kernel, calls = _kernel_client()
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    with TestClient(create_app(data_dir=tmp_path)) as client:
        first = client.post("/v1/kernel/audits", json=_request().model_dump(mode="json")).json()
        assert first["submission_status"] == "completed"
        second = client.post("/v1/kernel/workflows", json=_request().model_dump(mode="json")).json()
        assert second["idempotent_replay"] is True
        assert len(calls) == 5
        saved = client.get("/v1/kernel/workflows/request-1").json()
        assert saved["workflow_status"] == "completed"
        assert saved["statistics"]["run_submission_attempts"] == 1


def test_cli_submit_uses_same_journal_and_replays_result(tmp_path, monkeypatch, capsys):
    kernel, calls = _kernel_client()
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    monkeypatch.setenv("AGENTGUARD_DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "request.json"
    config.write_text(_request().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["agentguard", "kernel", "submit", "--config", str(config)])
    main()
    assert json.loads(capsys.readouterr().out)["submission_status"] == "completed"
    main()
    assert json.loads(capsys.readouterr().out)["idempotent_replay"] is True
    assert len(calls) == 5
