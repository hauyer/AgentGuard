from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient
from agentguard.api.app import create_app
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.memory.store import ExperienceStore
from agentguard.services.warden_lifecycle import WardenLifecycleService


class FakeKernel:
    def __init__(self, *, contracts=None, probes=None, saes=None, plan=None, run=None, poll_runs=None):
        self.contract_ids = contracts if contracts is not None else ["contract-1"]
        self.resources_by_kind = {
            "probe": probes if probes is not None else [{"resource_id": "probe-1", "state": "ready", "version": "1"}],
            "sae": saes if saes is not None else [{"resource_id": "sae-1", "state": "ready", "version": "1"}],
        }
        self.plan = plan or {"plan_id": "plan-1", "status": "planned", "blockers": []}
        self.initial_run = run or {"run_id": "run-1", "plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://audit-1"]}
        self.poll_runs = list(poll_runs or [])
        self.calls = []

    def health(self):
        self.calls.append(("health",))
        return {"status": "ok", "version": "0.2.0"}

    def capabilities(self):
        self.calls.append(("capabilities",))
        return [{"component_id": "activation.backend", "available": True}]

    def contracts(self):
        self.calls.append(("contracts",))
        return list(self.contract_ids)

    def resources(self, kind=None):
        self.calls.append(("resources", kind))
        return list(self.resources_by_kind.get(kind, []))

    def create_audit_plan(self, payload):
        self.calls.append(("create_audit_plan", dict(payload)))
        return dict(self.plan)

    def start_run(self, plan_id):
        self.calls.append(("start_run", plan_id))
        return dict(self.initial_run)

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        if self.poll_runs:
            return dict(self.poll_runs.pop(0))
        return dict(self.initial_run)


def request(**overrides):
    payload = {
        "request_id": "req-1",
        "contract_id": "contract-1",
        "model_id": "model-1",
        "runtime_id": "runtime-1",
        "mode": "causal_audit",
        "probe_id": "probe-1",
        "scenario_id": "scenario-1",
        "risk_type": "unauthorized_information_flow",
        "metadata": {"seed": 7},
    }
    payload.update(overrides)
    return WardenAuditRequest.model_validate(payload)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_preflight_blocks_missing_contract_without_creating_plan():
    kernel = FakeKernel(contracts=[])
    result = WardenLifecycleService(kernel).execute(request())
    assert result["status"] == "blocked"
    assert result["preflight"]["status"] == "blocked"
    assert "THREAT_CONTRACT_NOT_AVAILABLE:contract-1" in result["preflight"]["blockers"]
    assert not any(call[0] == "create_audit_plan" for call in kernel.calls)


def test_preflight_blocks_probe_not_ready():
    kernel = FakeKernel(probes=[{"resource_id": "probe-1", "state": "unavailable"}])
    result = WardenLifecycleService(kernel).execute(request())
    assert result["status"] == "blocked"
    assert any("PROBE_RESOURCE_NOT_READY" in item for item in result["preflight"]["blockers"])
    assert not any(call[0] == "create_audit_plan" for call in kernel.calls)


def test_warden_blocked_plan_never_starts_run():
    kernel = FakeKernel(plan={"plan_id": "plan-1", "status": "blocked", "blockers": ["Probe missing"]})
    result = WardenLifecycleService(kernel).execute(request())
    assert result["status"] == "blocked"
    assert result["run"] is None
    assert result["causal_claim_status"] == "not_assessed"
    assert not any(call[0] == "start_run" for call in kernel.calls)


def test_completed_run_with_explicit_refs_is_saved_as_unverified_experience(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.sqlite3")
    kernel = FakeKernel()
    result = WardenLifecycleService(kernel, experiences=store).execute(request())
    assert result["status"] == "completed"
    assert result["causal_claim_status"] == "awaiting_review"
    assert result["experience"]["verification_status"] == "unverified"
    assert result["experience"]["evidence_grade"] == "observational"
    assert result["experience"]["outcome"] == "inconclusive"
    assert result["experience"]["allow_auto_recommendation"] is False
    assert store.count() == 1
    # A retry for the same run is idempotent.
    again = WardenLifecycleService(kernel, experiences=store).execute(request())
    assert again["experience"]["experience_id"] == result["experience"]["experience_id"]
    assert store.count() == 1


def test_completed_run_without_refs_does_not_create_experience(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.sqlite3")
    kernel = FakeKernel(run={"run_id": "run-no-evidence", "plan_id": "plan-1", "status": "completed"})
    result = WardenLifecycleService(kernel, experiences=store).execute(request())
    assert result["status"] == "completed"
    assert result["causal_claim_status"] == "not_assessed"
    assert result["experience"] is None
    assert store.count() == 0


def test_failed_run_does_not_create_experience(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.sqlite3")
    kernel = FakeKernel(run={"run_id": "run-fail", "plan_id": "plan-1", "status": "failed", "evidence_refs": ["artifact://x"]})
    result = WardenLifecycleService(kernel, experiences=store).execute(request(), approval_valid=True)
    assert result["status"] == "failed"
    assert result["experience"] is None
    assert store.count() == 0


def test_unknown_status_fails_closed():
    kernel = FakeKernel(run={"run_id": "run-1", "plan_id": "plan-1", "status": "maybe-clean", "evidence_refs": ["artifact://x"]})
    result = WardenLifecycleService(kernel).execute(request())
    assert result["status"] == "unknown"
    assert result["experience"] is None


def test_wait_polls_until_completed_and_records_experience(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.sqlite3")
    clock = FakeClock()
    kernel = FakeKernel(
        run={"run_id": "run-1", "plan_id": "plan-1", "status": "running"},
        poll_runs=[
            {"run_id": "run-1", "plan_id": "plan-1", "status": "running"},
            {"run_id": "run-1", "plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://done"]},
        ],
    )
    result = WardenLifecycleService(kernel, experiences=store, sleep=clock.sleep, monotonic=clock.monotonic).execute(
        request(wait_for_completion=True, timeout_seconds=5, poll_interval_seconds=0.1)
    )
    assert result["status"] == "completed"
    assert result["experience"] is not None
    assert len([call for call in kernel.calls if call[0] == "get_run"]) == 2


def test_wait_timeout_is_bounded_and_never_records_experience(tmp_path):
    store = ExperienceStore(tmp_path / "experiences.sqlite3")
    clock = FakeClock()
    kernel = FakeKernel(
        run={"run_id": "run-1", "plan_id": "plan-1", "status": "running", "evidence_refs": ["artifact://not-terminal"]},
        poll_runs=[{"run_id": "run-1", "plan_id": "plan-1", "status": "running"}] * 10,
    )
    result = WardenLifecycleService(kernel, experiences=store, sleep=clock.sleep, monotonic=clock.monotonic).execute(
        request(wait_for_completion=True, timeout_seconds=1, poll_interval_seconds=0.2)
    )
    assert result["status"] == "timed_out"
    assert result["experience"] is None
    assert store.count() == 0
    assert clock.now <= 1.000001


def test_defense_evaluation_requires_approval_before_any_kernel_call():
    kernel = FakeKernel()
    result = WardenLifecycleService(kernel).execute(request(mode="defense_evaluation"))
    assert result["status"] == "blocked"
    assert result["preflight"]["blockers"] == ["OPERATOR_APPROVAL_REQUIRED"]
    assert kernel.calls == []


def test_kernel_workflow_api_runs_preflight_and_reports_blocked(tmp_path, monkeypatch):
    from agentguard.adapters.warden import WardenKernelClient

    class Transport:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, headers, body, timeout):
            self.calls.append((method, url, body))
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0"}
            elif url.endswith("/v1/capabilities"):
                payload = []
            elif url.endswith("/v1/contracts"):
                payload = []
            elif "/v1/resources" in url:
                payload = []
            else:
                raise AssertionError(f"Unexpected request: {url}")
            return 200, json.dumps(payload).encode()

    transport = Transport()
    kernel = WardenKernelClient("https://warden.example", transport=transport)
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.post("/v1/kernel/workflows", json=request().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert "THREAT_CONTRACT_NOT_AVAILABLE" in response.json()["preflight"]["blockers"][0]
    assert not any(url.endswith("/v1/audit-plans") for _, url, _ in transport.calls)


def test_real_client_boundary_full_lifecycle_uses_only_documented_warden_v1_routes(tmp_path):
    from agentguard.adapters.warden import WardenKernelClient

    class Transport:
        def __init__(self):
            self.calls = []
            self.responses = [
                (200, {"status": "ok", "version": "0.2.0"}),
                (200, [{"component_id": "activation.backend", "available": True}]),
                (200, ["contract-1"]),
                (200, [{"resource_id": "probe-1", "state": "ready", "version": "v1"}]),
                (200, {"plan_id": "plan-1", "status": "planned", "blockers": []}),
                (200, {"run_id": "run-1", "plan_id": "plan-1", "status": "running"}),
                (200, {"run_id": "run-1", "plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://trace-1"]}),
                (200, {"status": "ok", "version": "0.2.0"}),
            ]

        def __call__(self, method, url, headers, body, timeout):
            self.calls.append((method, url, json.loads(body) if body else None))
            status, payload = self.responses.pop(0)
            return status, json.dumps(payload).encode("utf-8")

    transport = Transport()
    client = WardenKernelClient("https://warden.example", transport=transport)
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    result = WardenLifecycleService(client, experiences=store, sleep=lambda _: None).execute(
        request(wait_for_completion=True, timeout_seconds=3, poll_interval_seconds=0.1)
    )
    assert result["status"] == "completed"
    assert result["experience"] is not None
    assert [call[1].removeprefix("https://warden.example") for call in transport.calls] == [
        "/v1/health", "/v1/capabilities", "/v1/contracts", "/v1/resources?kind=probe",
        "/v1/audit-plans", "/v1/runs", "/v1/runs/run-1", "/v1/health",
    ]
    forwarded = transport.calls[4][2]
    assert "scenario_id" not in forwarded
    assert "risk_type" not in forwarded
    assert "wait_for_completion" not in forwarded
    assert store.count() == 1


def test_kernel_client_rejects_malformed_list_payloads():
    from agentguard.adapters.warden import WardenKernelClient, WardenKernelError

    class Transport:
        def __call__(self, method, url, headers, body, timeout):
            return 200, json.dumps(["not-an-object"]).encode()

    client = WardenKernelClient("https://warden.example", transport=Transport())
    with pytest.raises(WardenKernelError, match="list of objects"):
        client.capabilities()


def test_malformed_run_response_missing_id_is_rejected():
    from agentguard.adapters.warden import WardenKernelError

    kernel = FakeKernel(run={"plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://x"]})
    with pytest.raises(WardenKernelError, match="missing run_id"):
        WardenLifecycleService(kernel).execute(request())


def test_workflow_journal_replays_completed_request_without_duplicate_submission(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    store = ExperienceStore(tmp_path / "experiences.sqlite3")
    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = FakeKernel()
    service = WardenLifecycleService(kernel, experiences=store, workflows=journal)
    first = service.execute(request())
    calls_after_first = len(kernel.calls)
    second = service.execute(request())
    assert first["status"] == "completed"
    assert second["idempotent_replay"] is True
    assert len(kernel.calls) == calls_after_first
    assert journal.get("req-1")["status"] == "finished"
    assert len(journal.events("req-1")) >= 5


def test_workflow_journal_rejects_request_id_reuse_with_different_payload(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore, WorkflowConflictError

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = FakeKernel()
    service = WardenLifecycleService(kernel, workflows=journal)
    service.execute(request())
    with pytest.raises(WorkflowConflictError):
        service.execute(request(model_id="different-model"))


def test_workflow_journal_does_not_resubmit_ambiguous_plan_submission(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = FakeKernel()
    request_value = request()
    payload = request_value.model_dump(mode="json")
    journal.begin(request_value.request_id, payload)
    journal.update(request_value.request_id, "plan_submitting", context={"stage": "plan_submitting"})
    service = WardenLifecycleService(kernel, workflows=journal)
    result = service.execute(request_value)
    assert result["status"] == "reconciliation_required"
    assert not any(call[0] == "create_audit_plan" for call in kernel.calls)


def test_workflow_journal_resumes_known_run_without_starting_duplicate(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = FakeKernel(run={"run_id": "run-recover", "plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://recovered"]})
    request_value = request(wait_for_completion=True)
    journal.begin(request_value.request_id, request_value.model_dump(mode="json"))
    journal.update(request_value.request_id, "run_submitted", context={
        "stage": "run_submitted",
        "preflight": {"status": "ready", "blockers": []},
        "plan": {"plan_id": "plan-1", "status": "planned"},
        "run": {"run_id": "run-recover", "plan_id": "plan-1", "status": "running"},
    })
    service = WardenLifecycleService(kernel, workflows=journal)
    result = service.execute(request_value)
    assert result["status"] == "completed"
    assert result["recovered"] is True
    assert not any(call[0] == "start_run" for call in kernel.calls)
    assert any(call[0] == "get_run" for call in kernel.calls)


def test_workflow_journal_is_persistent_and_records_append_only_events(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    path = tmp_path / "warden_workflows.sqlite3"
    first = WardenWorkflowStore(path)
    payload = request().model_dump(mode="json")
    entry, created = first.begin("persistent-request", payload)
    assert created is True
    first.update("persistent-request", "preflighting", context={"stage": "preflighting"})
    first.update("persistent-request", "blocked", result={"status": "blocked"})

    reopened = WardenWorkflowStore(path)
    entry = reopened.get("persistent-request")
    assert entry is not None
    assert entry["status"] == "blocked"
    assert entry["result"] == {"status": "blocked"}
    events = reopened.events("persistent-request")
    assert [event["to_status"] for event in events] == ["created", "preflighting", "blocked"]


def test_workflow_journal_concurrent_begin_is_single_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    path = tmp_path / "warden_workflows.sqlite3"
    payload = request().model_dump(mode="json")

    def begin_one(_):
        return WardenWorkflowStore(path).begin("same-request", payload)[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        created_flags = list(pool.map(begin_one, range(16)))
    assert sum(created_flags) == 1
    assert WardenWorkflowStore(path).count() == 1


def test_workflow_api_replays_same_request_and_exposes_audit_events(tmp_path, monkeypatch):
    from agentguard.adapters.warden import WardenKernelClient

    class Transport:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, headers, body, timeout):
            self.calls.append((method, url))
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0"}
            elif url.endswith("/v1/capabilities"):
                payload = []
            elif url.endswith("/v1/contracts"):
                payload = []
            elif "/v1/resources" in url:
                payload = []
            else:
                raise AssertionError(f"Unexpected request: {url}")
            return 200, json.dumps(payload).encode()

    transport = Transport()
    kernel = WardenKernelClient("https://warden.example", transport=transport)
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    client = TestClient(create_app(data_dir=tmp_path))
    payload = request().model_dump(mode="json")
    first = client.post("/v1/kernel/workflows", json=payload)
    calls_after_first = len(transport.calls)
    second = client.post("/v1/kernel/workflows", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert len(transport.calls) == calls_after_first
    status = client.get("/v1/kernel/workflows/req-1")
    events = client.get("/v1/kernel/workflows/req-1/events")
    assert status.status_code == 200
    assert status.json()["status"] == "finished"
    assert events.status_code == 200
    assert len(events.json()["items"]) >= 3


def test_workflow_api_conflicting_reuse_of_request_id_returns_409(tmp_path, monkeypatch):
    from agentguard.adapters.warden import WardenKernelClient

    class Transport:
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0"}
            elif url.endswith("/v1/capabilities"):
                payload = []
            elif url.endswith("/v1/contracts"):
                payload = []
            elif "/v1/resources" in url:
                payload = []
            else:
                raise AssertionError(f"Unexpected request: {url}")
            return 200, json.dumps(payload).encode()

    kernel = WardenKernelClient("https://warden.example", transport=Transport())
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    client = TestClient(create_app(data_dir=tmp_path))
    payload = request().model_dump(mode="json")
    assert client.post("/v1/kernel/workflows", json=payload).status_code == 200
    payload["model_id"] = "different-model"
    conflict = client.post("/v1/kernel/workflows", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_uncertain_plan_submission_is_never_retried_automatically(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class LostPlanResponseKernel(FakeKernel):
        def create_audit_plan(self, payload):
            self.calls.append(("create_audit_plan", dict(payload)))
            raise WardenKernelError("connection dropped after request write", code="WARDEN_UNREACHABLE")

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = LostPlanResponseKernel()
    service = WardenLifecycleService(kernel, workflows=journal)
    with pytest.raises(WardenKernelError):
        service.execute(request())
    assert journal.get("req-1")["status"] == "submission_uncertain"
    result = service.execute(request())
    assert result["status"] == "reconciliation_required"
    assert len([call for call in kernel.calls if call[0] == "create_audit_plan"]) == 1


def test_uncertain_run_submission_is_never_retried_automatically(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class LostRunResponseKernel(FakeKernel):
        def start_run(self, plan_id):
            self.calls.append(("start_run", plan_id))
            raise WardenKernelError("connection dropped after run request write", code="WARDEN_UNREACHABLE")

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = LostRunResponseKernel()
    service = WardenLifecycleService(kernel, workflows=journal)
    with pytest.raises(WardenKernelError):
        service.execute(request())
    assert journal.get("req-1")["status"] == "submission_uncertain"
    result = service.execute(request())
    assert result["status"] == "reconciliation_required"
    assert len([call for call in kernel.calls if call[0] == "start_run"]) == 1


def test_workflow_api_lists_durable_workflow_summaries(tmp_path, monkeypatch):
    from agentguard.adapters.warden import WardenKernelClient

    class Transport:
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0"}
            elif url.endswith("/v1/capabilities"):
                payload = []
            elif url.endswith("/v1/contracts"):
                payload = []
            elif "/v1/resources" in url:
                payload = []
            else:
                raise AssertionError(f"Unexpected request: {url}")
            return 200, json.dumps(payload).encode()

    kernel = WardenKernelClient("https://warden.example", transport=Transport())
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    client = TestClient(create_app(data_dir=tmp_path))
    payload = request().model_dump(mode="json")
    assert client.post("/v1/kernel/workflows", json=payload).status_code == 200
    listed = client.get("/v1/kernel/workflows?limit=10")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["items"][0]["request_id"] == "req-1"
    assert listed.json()["items"][0]["status"] == "finished"


def test_non_waiting_workflow_rechecks_same_active_run_on_retry(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = FakeKernel(run={"run_id": "run-progress", "plan_id": "plan-1", "status": "running"})
    service = WardenLifecycleService(kernel, workflows=journal)
    first = service.execute(request(wait_for_completion=False))
    assert first["status"] == "running"
    assert journal.get("req-1")["status"] == "run_submitted"

    kernel.initial_run = {"run_id": "run-progress", "plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://finished"]}
    second = service.execute(request(wait_for_completion=False))
    assert second["status"] == "completed"
    assert second["recovered"] is True
    assert len([call for call in kernel.calls if call[0] == "start_run"]) == 1
    assert len([call for call in kernel.calls if call[0] == "get_run"]) == 1
