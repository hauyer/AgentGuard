from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient
from agentguard.api.app import create_app
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.memory.store import ExperienceStore
from agentguard.services.warden_lifecycle import WardenLifecycleService


def _openapi_document():
    read = {"responses": {"200": {"description": "ok"}}}

    def write(schema_name):
        return {
            "requestBody": {"content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"},
            }}},
            "responses": {"200": {"description": "ok"}},
        }

    return {
        "openapi": "3.1.0", "info": {"title": "WARDEN API", "version": "0.3.0"},
        "paths": {
            "/v1/health": {"get": read},
            "/v1/capabilities": {"get": read},
            "/v1/resources": {"get": read},
            "/v1/contracts": {"get": read},
            "/v1/audit-plans": {"get": read, "post": write("AuditRequestSchema")},
            "/v1/audit-plans/{plan_id}": {"get": read},
            "/v1/runs": {"get": read, "post": write("RunRequestSchema")},
            "/v1/runs/{run_id}": {"get": read},
        },
        "components": {"schemas": {
            "AuditRequestSchema": {
                "type": "object",
                "required": ["request_id", "contract_id", "model_id", "runtime_id"],
                "properties": {
                    key: {"type": "string"}
                    for key in ("request_id", "contract_id", "model_id", "runtime_id")
                } | {
                    "mode": {"$ref": "#/components/schemas/AuditMode"},
                    "capture_targets": {
                        "type": "array", "items": {"$ref": "#/components/schemas/CaptureTargetSchema"},
                    },
                },
            },
            "AuditMode": {
                "type": "string",
                "enum": ["capture_only", "detection", "causal_audit", "defense_evaluation"],
            },
            "CaptureTargetSchema": {"type": "object", "properties": {
                "target_id": {"type": "string"}, "agent_ids": {"type": "array"},
            }},
            "RunRequestSchema": {
                "type": "object", "required": ["plan_id"],
                "properties": {"plan_id": {"type": "string"}},
            },
        }},
    }


def test_actual_warden_evidence_bundle_artifacts_are_referenced():
    run = {"run_id": "run-1", "plan_id": "plan-1", "status": "completed", "evidence": {
        "artifacts": [{"artifact_id": "artifact-1", "uri": "artifact://trace-1", "content_hash": "a" * 64}],
        "risk_scores": [], "findings": [], "metrics": {}, "limitations": [],
    }}
    # Artifact ids take precedence over URIs, matching the EvidenceGateway rule.
    assert WardenLifecycleService._evidence_refs(run) == ["artifact-1"]


def test_file_uris_are_dropped_unopened_and_never_crash_experience_recording():
    run = {"run_id": "run-2", "plan_id": "plan-1", "status": "completed", "evidence": {
        "artifacts": [
            {"artifact_id": "observation-summary-" + "b" * 64,
             "uri": "file:///D:/never/opened/summary.json"},
            {"uri": "file:///C:/other/local.bin"},
        ],
        "findings": [], "metrics": {}, "limitations": [],
    }}
    refs = WardenLifecycleService._evidence_refs(run)
    assert refs == ["observation-summary-" + "b" * 64]
    assert all(not ref.lower().startswith(("file:", "\\\\", "/")) for ref in refs)
    # The extracted refs must satisfy the ExperienceRecord contract that
    # previously crashed real completed runs.
    from agentguard.contracts.experience import ExperienceRecord

    ExperienceRecord(
        experience_id="exp-check", scenario_id="sc", risk_type="secret_leak",
        hypothesis_summary="s", action_type="observe", action_summary="a",
        outcome="inconclusive", evidence_grade="observational", evidence_refs=refs,
    )


def test_incompatible_warden_api_blocks_before_plan_submission():
    class OldKernel:
        def health(self):
            return {"status": "ok", "version": "0.1.0", "api_version": "v0"}

    result = WardenLifecycleService(OldKernel()).preflight(request())
    assert result["status"] == "blocked"
    assert result["blockers"] == ["WARDEN_API_VERSION_INCOMPATIBLE"]


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
        return {"status": "ok", "version": "0.2.0", "api_version": "v1"}

    def openapi_summary(self, *, force_refresh=False):
        return {
            "compatible": True, "missing_contract_items": [], "path_count": 8,
            "service_version": "0.2.0", "schema_sha256": "a" * 64,
            "whitebox_workflow_api_available": "unsupported",
            "intervention_api_available": "unsupported",
            "artifact_access_available": "unsupported",
        }

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

    def list_runs(self):
        self.calls.append(("list_runs",))
        return []

    def get_audit_plan(self, plan_id):
        self.calls.append(("get_audit_plan", plan_id))
        return {
            **dict(self.plan),
            "plan_id": plan_id,
            "request": request().to_warden_payload(),
        }


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


def test_observation_with_artifact_does_not_open_causal_claim_review():
    result = WardenLifecycleService(FakeKernel()).execute(
        request(mode="observation", probe_id=None)
    )
    assert result["status"] == "completed"
    assert result["causal_claim_status"] == "not_assessed"


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


def test_unknown_is_a_reconcilable_nonterminal_journal_state(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    journal.begin("unknown-state", request().model_dump(mode="json"))
    journal.update("unknown-state", "preflighting")
    journal.update("unknown-state", "plan_submitting")
    journal.update("unknown-state", "plan_created")
    journal.update("unknown-state", "unknown", result={"status": "unknown"})
    journal.update("unknown-state", "plan_created", detail={"reconciled": True})
    assert journal.get("unknown-state")["status"] == "plan_created"


def test_journaled_unknown_plan_status_is_reconciled_without_resubmission(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class UnknownPlanKernel(FakeKernel):
        def __init__(self):
            super().__init__(
                plan={"plan_id": "plan-unknown", "status": "future-plan-state"},
                run={"run_id": "run-after-reconcile", "plan_id": "plan-unknown",
                     "status": "completed", "evidence_refs": ["artifact://done"]},
            )
            self.remote_plan_status = "future-plan-state"

        def get_audit_plan(self, plan_id):
            self.calls.append(("get_audit_plan", plan_id))
            return {
                "plan_id": plan_id,
                "status": self.remote_plan_status,
                "request": request().to_warden_payload(),
            }

        def list_runs(self):
            self.calls.append(("list_runs",))
            return [dict(self.initial_run)] if self.remote_plan_status == "planned" else []

    kernel = UnknownPlanKernel()
    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    service = WardenLifecycleService(kernel, workflows=journal)

    first = service.execute(request())
    assert first["status"] == "unknown"
    assert journal.get("req-1")["status"] == "unknown"
    assert not any(call[0] == "start_run" for call in kernel.calls)

    still_unknown = service.execute(request())
    assert still_unknown["status"] == "unknown"
    assert len([call for call in kernel.calls if call[0] == "create_audit_plan"]) == 1
    assert len([call for call in kernel.calls if call[0] == "get_audit_plan"]) == 1
    assert not any(call[0] == "start_run" for call in kernel.calls)

    kernel.remote_plan_status = "planned"
    reconciled = service.execute(request())
    assert reconciled["status"] == "completed"
    assert journal.get("req-1")["status"] == "finished"
    assert len([call for call in kernel.calls if call[0] == "create_audit_plan"]) == 1
    assert len([call for call in kernel.calls if call[0] == "start_run"]) == 0
    assert len([call for call in kernel.calls if call[0] == "get_run"]) == 1


def test_unknown_journal_without_remote_id_never_resubmits(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    kernel = FakeKernel()
    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    journal.begin("req-1", request().model_dump(mode="json"))
    journal.update("req-1", "preflighting")
    journal.update("req-1", "plan_submitting")
    journal.update("req-1", "plan_created")
    journal.update(
        "req-1", "unknown", context={"stage": "unknown"},
        result={"status": "unknown"},
    )

    result = WardenLifecycleService(kernel, workflows=journal).execute(request())

    assert result["status"] == "reconciliation_required"
    assert journal.get("req-1")["status"] == "unknown"
    assert not any(call[0] in {"create_audit_plan", "start_run"} for call in kernel.calls)


def test_journaled_unknown_run_status_is_reconciled_by_id_without_resubmission(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    kernel = FakeKernel(run={
        "run_id": "run-unknown", "plan_id": "plan-1",
        "status": "future-run-state", "evidence_refs": ["artifact://done"],
    })
    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    service = WardenLifecycleService(kernel, workflows=journal)

    first = service.execute(request())
    assert first["status"] == "unknown"
    assert journal.get("req-1")["status"] == "unknown"

    still_unknown = service.execute(request())
    assert still_unknown["status"] == "unknown"
    assert [call for call in kernel.calls if call[0] == "get_run"] == [
        ("get_run", "run-unknown"),
    ]

    kernel.initial_run["status"] = "completed"
    reconciled = service.execute(request())
    assert reconciled["status"] == "completed"
    assert journal.get("req-1")["status"] == "finished"
    assert len([call for call in kernel.calls if call[0] == "create_audit_plan"]) == 1
    assert len([call for call in kernel.calls if call[0] == "start_run"]) == 1
    assert len([call for call in kernel.calls if call[0] == "get_run"]) == 2


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

    class Transport:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, headers, body, timeout):
            self.calls.append((method, url, body))
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0", "api_version": "v1"}
            elif url.endswith("/openapi.json"):
                payload = _openapi_document()
            elif url.endswith(("/v1/capabilities", "/v1/contracts")) or "/v1/resources" in url:
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

    class Transport:
        def __init__(self):
            self.calls = []
            self.responses = [
                (200, {"status": "ok", "version": "0.2.0", "api_version": "v1"}),
                (200, _openapi_document()),
                (200, [{"component_id": "activation.backend", "available": True}]),
                (200, ["contract-1"]),
                (200, [{"resource_id": "probe-1", "kind": "probe", "state": "ready", "version": "v1"}]),
                (200, {"plan_id": "plan-1", "status": "planned", "blockers": []}),
                (200, {"run_id": "run-1", "plan_id": "plan-1", "status": "running"}),
                (200, {"run_id": "run-1", "plan_id": "plan-1", "status": "completed", "evidence_refs": ["artifact://trace-1"]}),
                (200, {"status": "ok", "version": "0.2.0", "api_version": "v1"}),
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
        "/v1/health", "/openapi.json", "/v1/capabilities", "/v1/contracts",
        "/v1/resources?kind=probe",
        "/v1/audit-plans", "/v1/runs", "/v1/runs/run-1", "/v1/health",
    ]
    forwarded = transport.calls[5][2]
    assert "scenario_id" not in forwarded
    assert "risk_type" not in forwarded
    assert "wait_for_completion" not in forwarded
    assert store.count() == 1


def test_kernel_client_rejects_malformed_list_payloads():
    from agentguard.adapters.warden import WardenKernelError

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


def test_concurrent_same_request_id_submits_one_remote_plan(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    entered_submit = Event()
    release_submit = Event()

    class PausedKernel(FakeKernel):
        def create_audit_plan(self, payload):
            self.calls.append(("create_audit_plan", dict(payload)))
            entered_submit.set()
            assert release_submit.wait(5)
            return dict(self.plan)

    database = tmp_path / "warden_workflows.sqlite3"
    kernel = PausedKernel()
    first_service = WardenLifecycleService(
        kernel, workflows=WardenWorkflowStore(database),
    )
    second_service = WardenLifecycleService(
        kernel, workflows=WardenWorkflowStore(database),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_service.execute, request())
            assert entered_submit.wait(5)
            concurrent = second_service.execute(request())
            assert concurrent["status"] == "in_progress"
            assert [call[0] for call in kernel.calls].count("create_audit_plan") == 1
            release_submit.set()
            assert first.result(timeout=10)["status"] == "completed"
    finally:
        release_submit.set()
    assert [call[0] for call in kernel.calls].count("create_audit_plan") == 1
    assert [call[0] for call in kernel.calls].count("start_run") == 1


def test_workflow_api_replays_same_request_and_exposes_audit_events(tmp_path, monkeypatch):

    class Transport:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, headers, body, timeout):
            self.calls.append((method, url))
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0", "api_version": "v1"}
            elif url.endswith("/openapi.json"):
                payload = _openapi_document()
            elif url.endswith(("/v1/capabilities", "/v1/contracts")) or "/v1/resources" in url:
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

    class Transport:
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0", "api_version": "v1"}
            elif url.endswith("/openapi.json"):
                payload = _openapi_document()
            elif url.endswith(("/v1/capabilities", "/v1/contracts")) or "/v1/resources" in url:
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


def test_uncertain_plan_submission_recovers_unique_existing_plan_by_request_id(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    value = request()

    class LostPlanResponseKernel(FakeKernel):
        def __init__(self):
            super().__init__()
            self.remote_plan = {
                **self.plan, "request": value.to_warden_payload(),
            }

        def create_audit_plan(self, payload):
            self.calls.append(("create_audit_plan", dict(payload)))
            raise WardenKernelError("plan response was lost", code="WARDEN_TIMEOUT")

        def list_audit_plans(self):
            self.calls.append(("list_audit_plans",))
            return [dict(self.remote_plan)]

        def get_audit_plan(self, plan_id):
            self.calls.append(("get_audit_plan", plan_id))
            return dict(self.remote_plan)

    kernel = LostPlanResponseKernel()
    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    service = WardenLifecycleService(kernel, workflows=journal)
    with pytest.raises(WardenKernelError):
        service.execute(value)
    result = service.execute(value)
    assert result["status"] == "planned"
    assert result["plan"]["plan_id"] == "plan-1"
    assert result["reconciled"] is True
    assert journal.get(value.request_id)["status"] == "plan_created"
    assert [call[0] for call in kernel.calls].count("create_audit_plan") == 1
    assert not any(call[0] == "start_run" for call in kernel.calls)


def test_uncertain_plan_submission_with_no_list_match_remains_ambiguous(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class LostPlanResponseKernel(FakeKernel):
        def create_audit_plan(self, payload):
            self.calls.append(("create_audit_plan", dict(payload)))
            raise WardenKernelError("plan response was lost", code="WARDEN_TIMEOUT")

        def list_audit_plans(self):
            self.calls.append(("list_audit_plans",))
            return []

    kernel = LostPlanResponseKernel()
    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    service = WardenLifecycleService(kernel, workflows=journal)
    with pytest.raises(WardenKernelError):
        service.execute(request())
    result = service.execute(request())
    assert result["status"] == "reconciliation_required"
    assert result["blockers"] == ["WARDEN_PLAN_NOT_VISIBLE_NOT_PROVEN_ABSENT"]
    assert [call[0] for call in kernel.calls].count("create_audit_plan") == 1
    assert not any(call[0] == "start_run" for call in kernel.calls)


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


def test_restart_with_unknown_remote_run_never_resubmits(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    database = tmp_path / "warden_workflows.sqlite3"
    request_value = request()

    class LostRunResponseKernel(FakeKernel):
        def start_run(self, plan_id):
            self.calls.append(("start_run", plan_id))
            raise WardenKernelError("response lost", code="WARDEN_TIMEOUT")

    first_store = WardenWorkflowStore(database)
    first_kernel = LostRunResponseKernel()
    with pytest.raises(WardenKernelError):
        WardenLifecycleService(first_kernel, workflows=first_store).execute(request_value)
    assert first_store.get(request_value.request_id)["status"] == "submission_uncertain"

    class RestartedKernel(FakeKernel):
        def get_audit_plan(self, plan_id):
            self.calls.append(("get_audit_plan", plan_id))
            raise WardenKernelError(
                "plan is no longer visible after WARDEN restart",
                status_code=404,
                code="WARDEN_NOT_FOUND",
            )

    restarted_store = WardenWorkflowStore(database)
    restarted_kernel = RestartedKernel()
    recovered = WardenLifecycleService(
        restarted_kernel, workflows=restarted_store,
    ).execute(request_value)
    assert recovered["status"] == "reconciliation_required"
    assert not any(call[0] in {"create_audit_plan", "start_run"}
                   for call in restarted_kernel.calls)


def test_uncertain_run_submission_reconciles_one_matching_warden_run(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class LostRunResponseKernel(FakeKernel):
        def start_run(self, plan_id):
            self.calls.append(("start_run", plan_id))
            raise WardenKernelError("response lost", code="WARDEN_TIMEOUT")

        def list_runs(self):
            self.calls.append(("list_runs",))
            return [dict(self.initial_run)]

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    kernel = LostRunResponseKernel()
    service = WardenLifecycleService(kernel, workflows=journal)
    with pytest.raises(WardenKernelError):
        service.execute(request())
    recovered = service.execute(request())
    assert recovered["status"] == "completed"
    assert recovered["recovered"] is True
    assert journal.get("req-1")["status"] == "finished"
    assert len([call for call in kernel.calls if call[0] == "start_run"]) == 1


def test_uncertain_run_submission_stays_ambiguous_with_multiple_matches(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class AmbiguousKernel(FakeKernel):
        def list_runs(self):
            self.calls.append(("list_runs",))
            return [
                {"run_id": "run-a", "plan_id": "plan-1", "status": "completed"},
                {"run_id": "run-b", "plan_id": "plan-1", "status": "completed"},
            ]

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    value = request()
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "submission_uncertain", context={
        "stage": "run_submitting",
        "plan": {"plan_id": "plan-1", "status": "planned"},
    })
    kernel = AmbiguousKernel()
    result = WardenLifecycleService(kernel, workflows=journal).execute(value)
    assert result["status"] == "reconciliation_required"
    assert journal.get("req-1")["status"] == "submission_uncertain"
    assert not any(call[0] == "start_run" for call in kernel.calls)


def test_uncertain_run_submission_rejects_mismatched_remote_plan(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class WrongPlanKernel(FakeKernel):
        def get_audit_plan(self, plan_id):
            value = super().get_audit_plan(plan_id)
            value["request"]["request_id"] = "someone-else"
            return value

        def list_runs(self):
            return [dict(self.initial_run)]

    journal = WardenWorkflowStore(tmp_path / "warden_workflows.sqlite3")
    value = request()
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "submission_uncertain", context={
        "stage": "run_submitting",
        "plan": {"plan_id": "plan-1", "status": "planned"},
    })
    result = WardenLifecycleService(WrongPlanKernel(), workflows=journal).execute(value)
    assert result["status"] == "reconciliation_required"
    assert journal.get("req-1")["status"] == "submission_uncertain"


@pytest.mark.parametrize("changed", [
    {"probe_id": "different-probe"},
    {"sae_id": "different-sae"},
    {"capture_targets": [{"target_id": "layer-1", "agent_ids": ["agent-a"], "layer": 1}]},
    {"metadata": {"seed": 19}},
    {"unexpected": "must-be-rejected"},
])
def test_uncertain_recovery_compares_complete_kernel_request(tmp_path, changed):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class DifferentPlanKernel(FakeKernel):
        def get_audit_plan(self, plan_id):
            remote = super().get_audit_plan(plan_id)
            remote["request"].update(changed)
            return remote

        def list_runs(self):
            return [dict(self.initial_run)]

    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    value = request()
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "submission_uncertain", context={
        "stage": "run_submitting", "plan": {"plan_id": "plan-1"},
    })
    result = WardenLifecycleService(DifferentPlanKernel(), workflows=journal).execute(value)
    assert result["status"] == "reconciliation_required"
    assert journal.get(value.request_id)["status"] == "submission_uncertain"


def test_uncertain_recovery_normalizes_omitted_optional_defaults(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    value = request(probe_id=None, metadata={})

    class DefaultPlanKernel(FakeKernel):
        def get_audit_plan(self, plan_id):
            payload = value.to_warden_payload()
            for key in ("probe_id", "sae_id", "capture_targets", "metadata"):
                payload.pop(key)
            return {"plan_id": plan_id, "request": payload}

        def list_runs(self):
            return [dict(self.initial_run)]

    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "submission_uncertain", context={
        "stage": "run_submitting", "plan": {"plan_id": "plan-1"},
    })
    result = WardenLifecycleService(DefaultPlanKernel(), workflows=journal).execute(value)
    assert result["status"] == "completed"
    assert result["recovered"] is True


def test_poll_network_failure_retains_known_run_for_retry(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    class PollFailureKernel(FakeKernel):
        def get_run(self, run_id):
            self.calls.append(("get_run", run_id))
            if sum(call[0] == "get_run" for call in self.calls) == 1:
                raise WardenKernelError("temporary network loss", code="WARDEN_TIMEOUT")
            return {"run_id": run_id, "plan_id": "plan-1", "status": "completed"}

    kernel = PollFailureKernel(run={
        "run_id": "run-1", "plan_id": "plan-1", "status": "running",
        "evidence_refs": ["artifact://produced-before-status-confirmation"],
    })
    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    clock = FakeClock()
    experiences = ExperienceStore(tmp_path / "experiences.sqlite3")
    service = WardenLifecycleService(
        kernel, workflows=journal, experiences=experiences,
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    value = request(wait_for_completion=True)
    with pytest.raises(WardenKernelError):
        service.execute(value)
    assert journal.get(value.request_id)["status"] == "run_submitted"
    assert experiences.count() == 0
    assert service.execute(value)["status"] == "completed"
    assert any(
        event["detail"].get("error_code") == "WARDEN_TIMEOUT"
        for event in journal.events(value.request_id)
    )
    assert sum(call[0] == "create_audit_plan" for call in kernel.calls) == 1
    assert sum(call[0] == "start_run" for call in kernel.calls) == 1


def test_legacy_interrupted_workflow_with_run_id_is_not_resubmitted(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    value = request()
    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "interrupted", context={
        "stage": "polling", "plan": {"plan_id": "plan-1"},
        "run": {"run_id": "run-1", "plan_id": "plan-1", "status": "running"},
    })
    kernel = FakeKernel()
    result = WardenLifecycleService(kernel, workflows=journal).execute(value)
    assert result["status"] == "completed"
    assert not any(call[0] in {"create_audit_plan", "start_run"} for call in kernel.calls)


@pytest.mark.parametrize("invalid_run", [
    {"plan_id": "plan-1", "status": "completed"},
    {"run_id": "run-1", "plan_id": "different-plan", "status": "completed"},
])
def test_bad_submission_response_preserves_uncertainty_without_resubmit(tmp_path, invalid_run):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    kernel = FakeKernel(run=invalid_run)
    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    service = WardenLifecycleService(kernel, workflows=journal)
    with pytest.raises(WardenKernelError):
        service.execute(request())
    assert journal.get("req-1")["status"] == "submission_uncertain"
    assert service.execute(request())["status"] == "reconciliation_required"
    assert sum(call[0] == "start_run" for call in kernel.calls) == 1


def test_resume_cannot_submit_defense_run_without_current_approval(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    value = request(mode="defense_evaluation")
    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "plan_created", context={"plan": {"plan_id": "plan-1"}})
    kernel = FakeKernel()
    result = WardenLifecycleService(kernel, workflows=journal).execute(value)
    assert result["status"] == "blocked"
    assert kernel.calls == []
    assert journal.get(value.request_id)["status"] == "plan_created"


def test_every_observed_remote_status_change_is_journaled(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    kernel = FakeKernel(
        run={"run_id": "run-1", "plan_id": "plan-1", "status": "queued"},
        poll_runs=[
            {"run_id": "run-1", "plan_id": "plan-1", "status": "running"},
            {"run_id": "run-1", "plan_id": "plan-1", "status": "completed"},
        ],
    )
    clock = FakeClock()
    store = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    result = WardenLifecycleService(
        kernel, workflows=store, sleep=clock.sleep, monotonic=clock.monotonic,
    ).execute(request(wait_for_completion=True))
    assert result["workflow_status"] == "completed"
    transitions = [
        (event["detail"]["remote_status_from"], event["detail"]["remote_status_to"])
        for event in store.events("req-1") if "remote_status_to" in event["detail"]
    ]
    assert transitions == [("queued", "running"), ("running", "completed")]


def test_workflow_api_lists_durable_workflow_summaries(tmp_path, monkeypatch):

    class Transport:
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/v1/health"):
                payload = {"status": "ok", "version": "0.2.0", "api_version": "v1"}
            elif url.endswith("/openapi.json"):
                payload = _openapi_document()
            elif url.endswith(("/v1/capabilities", "/v1/contracts")) or "/v1/resources" in url:
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


def test_operator_reconciliation_queries_known_run_without_any_submission(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "reconcile.sqlite3")
    value = request()
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "preflighting")
    journal.update(value.request_id, "plan_submitting")
    journal.update(value.request_id, "plan_created", context={
        "plan": {"plan_id": "plan-1"}, "preflight": {"status": "ready", "blockers": []},
    })
    journal.update(value.request_id, "run_submitting")
    journal.update(value.request_id, "run_submitted", context={
        "plan": {"plan_id": "plan-1"},
        "run": {"run_id": "run-1", "plan_id": "plan-1", "status": "running"},
    })
    kernel = FakeKernel(run={
        "run_id": "run-1", "plan_id": "plan-1", "status": "completed",
        "evidence_refs": ["artifact://observed"],
    })

    result = WardenLifecycleService(kernel, workflows=journal).reconcile_existing(value.request_id)

    assert result["status"] == "completed"
    assert result["reconciled"] is True
    assert journal.get(value.request_id)["status"] == "finished"
    assert [call[0] for call in kernel.calls] == ["get_run"]


def test_query_only_reconciliation_never_starts_a_run_from_a_planned_plan(tmp_path):
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "planned.sqlite3")
    value = request()
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "plan_created", context={
        "plan": {"plan_id": "plan-1"},
    })
    kernel = FakeKernel()

    result = WardenLifecycleService(kernel, workflows=journal).reconcile_existing(value.request_id)

    assert result["status"] == "reconciliation_required"
    assert "WARDEN_RUN_NOT_FOUND" in result["blockers"]
    assert journal.get(value.request_id)["status"] == "plan_created"
    assert [call[0] for call in kernel.calls] == ["get_audit_plan", "list_runs"]


def test_unavailable_known_run_stays_unknown_and_is_not_resubmitted(tmp_path):
    from agentguard.adapters.warden import WardenKernelError
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    journal = WardenWorkflowStore(tmp_path / "unknown-run.sqlite3")
    value = request()
    journal.begin(value.request_id, value.model_dump(mode="json"))
    journal.update(value.request_id, "preflighting")
    journal.update(value.request_id, "plan_submitting")
    journal.update(value.request_id, "plan_created", context={"plan": {"plan_id": "plan-1"}})
    journal.update(value.request_id, "run_submitting")
    journal.update(value.request_id, "run_submitted", context={"plan": {"plan_id": "plan-1"},
        "run": {"run_id": "run-1", "plan_id": "plan-1", "status": "running"}})
    journal.update(value.request_id, "unknown", context={
        "plan": {"plan_id": "plan-1"},
        "run": {"run_id": "run-1", "plan_id": "plan-1", "status": "mystery"},
    })

    class MissingRun(FakeKernel):
        def get_run(self, run_id):
            self.calls.append(("get_run", run_id))
            raise WardenKernelError("not found", status_code=404, code="WARDEN_HTTP_ERROR")

    kernel = MissingRun()
    result = WardenLifecycleService(kernel, workflows=journal).reconcile_existing(value.request_id)
    assert result["status"] == "reconciliation_required"
    assert journal.get(value.request_id)["status"] == "unknown"
    assert [call[0] for call in kernel.calls] == ["get_run"]
