"""Contract tests for standardized observation runs (D1)."""

import json

import pytest

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.run_config import InvestigationRunConfig
from agentguard.services.observation_workflow import (
    ObservationRunService,
    RunResolutionError,
)
from agentguard.services.warden_workflow_store import WardenWorkflowStore


class FakeKernel:
    def __init__(self, *, contracts=None, runtime_available=True, runtime_ids=None,
                 activation_available=False, plan=None, run=None):
        self.base_url = "https://warden.invalid"
        self._contract_ids = contracts if contracts is not None else ["contract-1"]
        runtime_ids = runtime_ids if runtime_ids is not None else ["stegopot.v1", "model-7b"]
        metadata = {}
        if runtime_available:
            metadata = {"runtime_id": runtime_ids[0],
                        "model_ids": runtime_ids[1:]} if len(runtime_ids) > 1 else {}
        components = [
            {"component_id": "agent.runtime", "available": runtime_available,
             "capabilities": [], "metadata": metadata},
            {"component_id": "run.dispatcher", "available": True, "capabilities": []},
            {"component_id": "activation.backend", "available": activation_available,
             "capabilities": []},
        ]
        self._components = components
        self._plan = plan or {"plan_id": "plan-obs-1", "status": "planned", "blockers": []}
        self._run = run
        self.calls = []

    def health(self):
        self.calls.append("health")
        return {"status": "ok", "version": "0.2.0", "api_version": "v1"}

    def openapi_summary(self, *, force_refresh=False):
        return {
            "compatible": True, "openapi_version": "3.1.0", "service_version": "0.3.0",
            "path_count": 8, "schema_sha256": "a" * 64,
        }

    def capabilities(self):
        return self._components

    def resources(self, kind=None):
        return []

    def contracts(self):
        return self._contract_ids

    def create_audit_plan(self, payload):
        self.calls.append(("create_audit_plan", payload))
        return dict(self._plan)

    def start_run(self, plan_id):
        self.calls.append(("start_run", plan_id))
        return dict(self._run or {})

    def get_run(self, run_id):
        return dict(self._run or {})


def _completed_run():
    return {
        "run_id": "run-obs-1", "plan_id": "plan-obs-1", "status": "completed",
        "evidence": {
            "artifacts": [
                {"artifact_id": "observation-summary-" + "a" * 64,
                 "uri": "file:///D:/never/opened.json"},
            ],
            "findings": [{"kind": "observational_run",
                          "messages": [{"message_id": "msg-1", "sender": "a", "recipient": "b"}]}],
            "metrics": {"trace_alignment.duration_seconds": 5.0},
        },
    }


def _config(**overrides):
    defaults = {
        "contract_id": "contract-1",
        "model_id": "model-7b",
        "runtime_id": "stegopot.v1",
        "audit_mode": "observation",
        "random_seed": 7,
        "scenario_id": "sc-fixed",
    }
    defaults.update(overrides)
    return InvestigationRunConfig(**defaults)


def _service(tmp_path, kernel):
    return ObservationRunService(
        client=kernel,
        workflows=WardenWorkflowStore(tmp_path / "workflows.sqlite3"),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
    )


def test_resolve_ready_with_kernel_declared_identifiers(tmp_path):
    service = _service(tmp_path, FakeKernel())
    report = service.resolve(_config())
    assert report["status"] == "ready"
    assert report["blockers"] == []
    assert report["declared_runtime_ids"] == ["stegopot.v1", "model-7b"]
    assert report["contracts_count"] == 1


def test_resolve_blocks_undeclared_ids_and_missing_contract(tmp_path):
    service = _service(tmp_path, FakeKernel())
    report = service.resolve(_config(model_id="demo-model", runtime_id="runtime-b11"))
    assert report["status"] == "blocked"
    assert "MODEL_ID_NOT_DECLARED:demo-model" in report["blockers"]
    assert "RUNTIME_ID_NOT_DECLARED:runtime-b11" in report["blockers"]
    missing = service.resolve(_config(contract_id="contract-qwb-001"))
    assert "THREAT_CONTRACT_NOT_AVAILABLE:contract-qwb-001" in missing["blockers"]


def test_resolve_blocks_activation_modes_without_activation_backend(tmp_path):
    service = _service(tmp_path, FakeKernel(activation_available=False))
    causal = service.resolve(_config(audit_mode="causal_audit"))
    assert "MODE_NOT_SUPPORTED_WITHOUT_ACTIVATION_BACKEND:causal_audit" in causal["blockers"]
    observation = service.resolve(_config(audit_mode="observation"))
    assert observation["status"] == "ready"


def test_execute_records_completed_observation_with_safe_refs(tmp_path):
    kernel = FakeKernel(run=_completed_run())
    service = _service(tmp_path, kernel)
    record = service.execute(_config())
    assert record.execution_status == "completed"
    assert record.data_source == "warden_observation"
    assert record.run_id == "run-obs-1"
    assert record.plan_id == "plan-obs-1"
    assert record.evidence_refs == ["observation-summary-" + "a" * 64]
    assert "file:" not in json.dumps(record.evidence_refs)
    assert any("Observation-only" in note for note in record.limitations)
    assert record.run_config_artifact_id and record.record_artifact_id
    entry = service._workflows.get(record.request_id)
    assert entry is not None and entry["status"] == "finished"
    # The kernel request never received AgentGuard-only study fields.
    payload = next(call[1] for call in kernel.calls if call[0] == "create_audit_plan")
    for banned in ("investigation_budget", "time_budget", "model_call_budget",
                   "data_partition", "source_commit"):
        assert banned not in payload
    assert payload["metadata"]["seed"] == 7


def test_execute_blocked_resolution_never_contacts_the_kernel(tmp_path):
    kernel = FakeKernel(run=_completed_run())
    service = _service(tmp_path, kernel)
    record = service.execute(_config(contract_id="contract-missing"))
    assert record.execution_status == "blocked"
    assert record.data_source == "none"
    assert any(b.startswith("THREAT_CONTRACT_NOT_AVAILABLE") for b in record.blockers)
    assert not any(isinstance(call, tuple) and call[0] == "create_audit_plan"
                   for call in kernel.calls)


def test_execute_is_idempotent_for_the_same_fixed_config(tmp_path):
    kernel = FakeKernel(run=_completed_run())
    service = _service(tmp_path, kernel)
    config = _config()  # one fixed configuration object; replays idempotently
    first = service.execute(config)
    second = service.execute(config)
    assert first.request_id == second.request_id
    assert any("Idempotent replay" in note for note in second.limitations)
    plan_calls = [call for call in kernel.calls
                  if isinstance(call, tuple) and call[0] == "create_audit_plan"]
    assert len(plan_calls) == 1


def test_execute_with_distinct_request_id_creates_new_runs(tmp_path):
    kernel = FakeKernel(run=_completed_run())
    service = _service(tmp_path, kernel)
    first = service.execute(_config(), request_id="repetition-1")
    second = service.execute(_config(), request_id="repetition-2")
    assert first.request_id != second.request_id
    plan_calls = [call for call in kernel.calls
                  if isinstance(call, tuple) and call[0] == "create_audit_plan"]
    assert len(plan_calls) == 2


def test_execute_records_failure_without_blind_retry(tmp_path):
    class ExplodingKernel(FakeKernel):
        def start_run(self, plan_id):
            raise RuntimeError("kernel exploded")

    service = _service(tmp_path, ExplodingKernel(run=_completed_run()))
    record = service.execute(_config())
    assert record.execution_status == "failed"
    assert any(b.startswith("EXECUTION_ERROR") for b in record.blockers)
    # Journal holds the interrupted/ambiguous state for reconciliation.
    entry = service._workflows.get(record.request_id)
    assert entry["status"] in {"interrupted", "run_submitted", "submission_uncertain"}


def test_record_and_config_artifacts_carry_no_message_bodies(tmp_path):
    kernel = FakeKernel(run=_completed_run())
    service = _service(tmp_path, kernel)
    record = service.execute(_config())
    config_payload = service._artifacts.get_bytes(record.run_config_artifact_id).decode("utf-8")
    record_payload = service._artifacts.get_bytes(record.record_artifact_id).decode("utf-8")
    for text in (config_payload, record_payload):
        assert "observation-summary" not in text or "msg-1" not in text
        assert "sender" not in text
        assert "content" not in text
        assert "ground_truth" not in text


def test_run_config_contract_guards():
    with pytest.raises(ValueError):
        InvestigationRunConfig(
            contract_id="c", model_id="m", runtime_id="r",
            metadata={"ground_truth": 1},
        )
    with pytest.raises(ValueError):
        InvestigationRunConfig(
            contract_id="c", model_id="m", runtime_id="r",
            data_partition="training", random_seed=None,
        )


def test_unconfigured_kernel_raises_resolution_error(tmp_path):
    service = ObservationRunService(
        client_provider=lambda: (None, "WARDEN_URL_NOT_CONFIGURED"),
        workflows=WardenWorkflowStore(tmp_path / "w.sqlite3"),
        artifacts=LocalArtifactStore(tmp_path / "a"),
    )
    with pytest.raises(RunResolutionError):
        service.resolve(_config())
