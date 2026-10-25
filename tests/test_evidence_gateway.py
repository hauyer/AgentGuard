import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentguard.adapters.mock import MockBackend
from agentguard.api.app import create_app
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.evidence import CausalValidationCriteria, GovernedEvidence
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine
from agentguard.services.evidence_gateway import EvidenceGateway


def _report():
    backend = MockBackend()
    return AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security",
        runtime_backend="mock",
        auto_intervene=False,
    ))


def test_report_evidence_is_governed_and_synthetic_stays_unverified(tmp_path):
    report = _report()
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    report_artifact = artifacts.put(
        report.model_dump_json().encode(),
        metadata={"run_id": report.run_id, "backend_mode": "synthetic"},
        tags=["run-report", "synthetic"],
    )

    items = EvidenceGateway().for_report(
        report,
        report_artifact_id=report_artifact.artifact_ref.artifact_id,
        artifacts=artifacts,
    )

    assert items
    assert all(item.source_system for item in items)
    assert all(item.evidence_level == "synthetic" for item in items)
    assert all(item.verification_status == "unverified" for item in items)
    assert all(item.run_status == "completed" for item in items)
    assert all(item.confidence is None for item in items)
    assert all(any("Synthetic" in limitation for limitation in item.limitations) for item in items)


def test_warden_evidence_requires_completed_matching_run_and_filters_local_uris(tmp_path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    gateway = EvidenceGateway()
    entry = {
        "request_id": "workflow-1",
        "workflow_status": "completed",
        "run_id": "run-1",
        "request": {
            "scenario_id": "family-a",
            "risk_type": "secret_flow",
            "model_id": "local-7b",
            "metadata": {"scenario_version": "v2"},
        },
        "context": {
            "plan": {"plan_id": "plan-1"},
            "run": {
                "run_id": "run-1",
                "plan_id": "plan-1",
                "status": "completed",
                "evidence_bundle": {
                    "items": [{
                        "evidence_id": "ev-1",
                        "type": "intervention",
                        "target_type": "message",
                        "target_ref": "msg-17",
                        "evidence_level": "causal_validated",
                        "intervention_status": "completed",
                        "artifact_refs": ["artifact://warden/ref-1", "file:///C:/private/secret.bin"],
                        "causal_validation": {
                            "independent_repetitions": 3,
                            "matched_control_passed": True,
                            "negative_control_passed": True,
                            "benign_utility_measured": True,
                            "artifact_hashes_verified": True,
                            "alternative_explanations_reviewed": True,
                        },
                    }],
                },
            },
        },
        "result": {"status": "completed"},
    }

    response = gateway.for_warden_workflow(entry, artifacts=artifacts)
    assert response["run_status"] == "completed"
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert item["warden_run_id"] == "run-1"
    assert item["warden_plan_id"] == "plan-1"
    assert item["target_type"] == "message"
    assert item["target_ref"] == "msg-17"
    assert item["evidence_level"] == "interventional"
    assert item["verification_status"] == "unverified"
    assert item["artifact_refs"] == ["artifact://warden/ref-1"]
    assert any("no path was opened" in note for note in item["limitations"])

    entry["context"]["run"]["status"] = "failed"
    failed = gateway.for_warden_workflow(entry, artifacts=artifacts)
    assert failed["run_status"] == "failed"
    assert failed["items"] == []


@pytest.mark.parametrize("source_kind", ["synthetic", "stub"])
def test_synthetic_or_stub_workflow_is_not_emitted_as_warden_evidence(tmp_path, source_kind):
    entry = {
        "request_id": "synthetic-workflow",
        "workflow_status": "completed",
        "run_id": "synthetic-run",
        "request": {"metadata": {"source_kind": source_kind}},
        "context": {"run": {
            "run_id": "synthetic-run",
            "status": "completed",
            "evidence": {"findings": [{"id": "synthetic-finding"}]},
        }},
        "result": {"status": "completed"},
    }

    response = EvidenceGateway().for_warden_workflow(
        entry, artifacts=LocalArtifactStore(tmp_path / source_kind),
    )

    assert response["run_status"] == "completed"
    assert response["items"] == []
    assert any(f"source_kind={source_kind}" in note for note in response["limitations"])


def test_causal_validated_contract_requires_provenance_and_all_method_checks():
    with pytest.raises(ValidationError):
        GovernedEvidence(
            evidence_id="ev-1",
            source_system="WARDEN",
            evidence_type="evaluation",
            evidence_level="causal_validated",
            verification_status="unverified",
            run_status="completed",
        )


def test_workflow_evidence_api_uses_gateway_and_does_not_expose_raw_warden_bundle(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    entry = {
        "request_id": "workflow-api",
        "workflow_status": "completed",
        "run_id": "run-api",
        "request": {"scenario_id": "case-a", "risk_type": "data_flow", "model_id": "local-3b"},
        "context": {
            "plan": {"plan_id": "plan-api"},
            "run": {
                "run_id": "run-api",
                "plan_id": "plan-api",
                "status": "completed",
                "evidence_refs": ["file:///C:/private/warden.bin", "artifact://warden/evidence-1"],
            },
        },
        "result": {"status": "completed"},
    }
    app.state.warden_workflows.get = lambda request_id: entry if request_id == "workflow-api" else None

    with TestClient(app) as client:
        response = client.get("/v1/kernel/workflows/workflow-api/evidence")
        assert response.status_code == 200
        payload = response.json()
        assert payload["items"][0]["source_system"] == "WARDEN"
        assert payload["items"][0]["artifact_refs"] == ["artifact://warden/evidence-1"]
        assert "file:///C:/private/warden.bin" not in response.text
        assert payload["items"][0]["verification_status"] == "unverified"

    with pytest.raises(ValidationError):
        CausalValidationCriteria(
            independent_repetitions=2,
            matched_control_passed=True,
            negative_control_passed=False,
            benign_utility_measured=True,
            artifact_hashes_verified=True,
            alternative_explanations_reviewed=True,
        )


@pytest.mark.parametrize("unsafe_ref", [
    "file:///C:/private/run.bin",
    "D:\\private\\run.bin",
    "https://user:secret@example.test/artifact",
    "https://example.test/artifact?token=secret",
])
def test_governed_evidence_rejects_local_paths_and_credential_urls(unsafe_ref):
    with pytest.raises(ValidationError):
        GovernedEvidence(
            evidence_id="ev-1",
            source_system="WARDEN",
            evidence_type="run_summary",
            evidence_level="observational",
            artifact_refs=[unsafe_ref],
        )
