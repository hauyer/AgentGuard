import json

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
                        "methodology_reviewed": True,
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
    assert item["source_authenticity_status"] == "unknown"
    assert item["methodology_review_status"] == "unverified"
    assert item["causal_validation"] is None
    assert item["artifact_refs"] == ["artifact://warden/ref-1"]
    assert any("no path was opened" in note for note in item["limitations"])
    assert any("upstream" in note for note in item["limitations"])

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
            independent_run_ids=["run-1", "run-1"],
            sample_ids=["sample-1", "sample-2"],
            experiment_config_sha256="a" * 64,
            matched_control_passed=True,
            matched_control_artifact_ref="artifact://matched",
            negative_control_passed=True,
            negative_control_artifact_ref="artifact://negative",
            benign_utility_measured=True,
            utility_artifact_ref="artifact://utility",
            artifact_hashes_verified=True,
            alternative_explanations_reviewed=True,
            alternative_explanations_review_ref="artifact://alternatives",
            methodology_review_record_ref="artifact://review",
            methodology_reviewer_id="reviewer-1",
        )


def test_upstream_causal_booleans_cannot_upgrade_without_trusted_proof(tmp_path):
    entry = {
        "request_id": "workflow-claim", "workflow_status": "completed", "run_id": "run-claim",
        "request": {"scenario_id": "case-a", "metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-claim"},
            "run": {
                "run_id": "run-claim", "plan_id": "plan-claim", "status": "completed",
                "evidence": {"items": [{
                    "evidence_id": "claim", "type": "evaluation",
                    "evidence_level": "causal_validated", "methodology_reviewed": True,
                    "causal_validation": {
                        "independent_repetitions": 10,
                        "matched_control_passed": True,
                        "negative_control_passed": True,
                        "benign_utility_measured": True,
                        "artifact_hashes_verified": True,
                        "alternative_explanations_reviewed": True,
                    },
                    "artifact_refs": ["https://warden.invalid/evidence"],
                }]},
            },
        },
        "result": {"status": "completed"},
    }
    projected = EvidenceGateway().for_warden_workflow(
        entry, artifacts=LocalArtifactStore(tmp_path / "artifacts"),
    )
    item = projected["items"][0]
    assert item["evidence_level"] == "observational"
    assert item["verification_status"] == "unverified"
    assert item["source_authenticity_status"] == "unknown"
    assert item["methodology_review_status"] == "unverified"
    assert item["causal_validation"] is None


def test_conflicting_control_result_cannot_support_causal_grade(tmp_path):
    entry = {
        "request_id": "workflow-conflicting-control", "workflow_status": "completed",
        "run_id": "run-control", "request": {"metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-control"},
            "run": {
                "run_id": "run-control", "plan_id": "plan-control", "status": "completed",
                "evidence": {"items": [{
                    "type": "evaluation", "evidence_level": "causal_validated",
                    "causal_validation": {
                        "independent_repetitions": 9,
                        "matched_control_passed": False,
                        "negative_control_passed": True,
                        "benign_utility_measured": True,
                        "artifact_hashes_verified": True,
                        "alternative_explanations_reviewed": True,
                    },
                    "artifact_refs": ["https://warden.invalid/control-result"],
                }]},
            },
        },
    }
    response = EvidenceGateway().for_warden_workflow(
        entry, artifacts=LocalArtifactStore(tmp_path / "artifacts"),
    )
    assert response["items"][0]["evidence_level"] == "observational"
    assert response["items"][0]["causal_validation"] is None


def test_evidence_item_from_another_run_is_omitted(tmp_path):
    entry = {
        "request_id": "workflow-cross-run", "workflow_status": "completed", "run_id": "run-current",
        "request": {"scenario_id": "case-a", "metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-current"},
            "run": {
                "run_id": "run-current", "plan_id": "plan-current", "status": "completed",
                "evidence": {"items": [{
                    "evidence_id": "foreign", "type": "observation",
                    "run_id": "run-other", "plan_id": "plan-current",
                    "artifact_refs": ["artifact://external/foreign"],
                }]},
            },
        },
        "result": {"status": "completed"},
    }
    response = EvidenceGateway().for_warden_workflow(
        entry, artifacts=LocalArtifactStore(tmp_path / "artifacts"),
    )
    assert response["items"] == []
    assert any("different run or plan" in note for note in response["limitations"])


def test_local_artifact_hash_and_run_plan_manifest_are_checked(tmp_path):
    import hashlib

    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    body = json.dumps({
        "run_id": "run-local", "plan_id": "plan-local",
        "manifest": {
            "run_id": "run-local", "plan_id": "plan-local",
            "content_sha256": "0" * 64,
        },
    }).encode()
    ref = artifacts.put(
        body, metadata={"run_id": "run-local", "plan_id": "plan-local"},
    ).artifact_ref.uri
    entry = {
        "request_id": "workflow-local", "workflow_status": "completed", "run_id": "run-local",
        "request": {"scenario_id": "case-a", "metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-local"},
            "run": {
                "run_id": "run-local", "plan_id": "plan-local", "status": "completed",
                "evidence": {"items": [{
                    "evidence_id": "local", "type": "evaluation",
                    "artifact_refs": [ref],
                }]},
            },
        },
        "result": {"status": "completed"},
    }
    item = EvidenceGateway().for_warden_workflow(entry, artifacts=artifacts)["items"][0]
    assert item["artifact_refs"] == []
    assert item["content_integrity_status"] == "failed"
    assert item["source_authenticity_status"] == "unknown"
    assert item["evidence_level"] == "observational"
    assert hashlib.sha256(body).hexdigest() in ref


def test_local_artifact_modified_content_or_ownership_metadata_is_rejected(tmp_path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    body = json.dumps({"run_id": "run-other", "plan_id": "plan-owned"}).encode()
    artifact = artifacts.put(
        body, metadata={"run_id": "run-other", "plan_id": "plan-owned"},
    )
    digest = artifact.artifact_ref.artifact_id
    _, metadata_path = artifacts._paths(digest)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["metadata"]["run_id"] = "run-owned"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    entry = {
        "request_id": "workflow-owned", "workflow_status": "completed", "run_id": "run-owned",
        "request": {"metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-owned"},
            "run": {
                "run_id": "run-owned", "plan_id": "plan-owned", "status": "completed",
                "evidence": {"items": [{
                    "type": "observation", "artifact_refs": [artifact.artifact_ref.uri],
                }]},
            },
        },
    }
    changed_metadata = EvidenceGateway().for_warden_workflow(entry, artifacts=artifacts)
    assert changed_metadata["items"][0]["artifact_refs"] == []
    assert changed_metadata["items"][0]["content_integrity_status"] == "failed"

    valid_body = json.dumps({"run_id": "run-owned", "plan_id": "plan-owned"}).encode()
    valid_artifact = artifacts.put(
        valid_body, metadata={"run_id": "run-owned", "plan_id": "plan-owned"},
    )
    entry["context"]["run"]["evidence"]["items"][0]["artifact_refs"] = [
        valid_artifact.artifact_ref.uri,
    ]
    tampered_blob, _ = artifacts._paths(valid_artifact.artifact_ref.artifact_id)
    tampered_blob.write_bytes(b"tampered content")
    changed_content = EvidenceGateway().for_warden_workflow(entry, artifacts=artifacts)
    assert changed_content["items"][0]["artifact_refs"] == []
    assert changed_content["items"][0]["content_integrity_status"] == "failed"


@pytest.mark.parametrize("source_kind", ["synthetic", "stub"])
def test_synthetic_or_stub_item_is_omitted_even_inside_real_run(tmp_path, source_kind):
    entry = {
        "request_id": "workflow-tagged", "workflow_status": "completed", "run_id": "run-tagged",
        "request": {"scenario_id": "case-a", "metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-tagged"},
            "run": {
                "run_id": "run-tagged", "plan_id": "plan-tagged", "status": "completed",
                "evidence": {"items": [{
                    "evidence_id": "tagged", "type": "evaluation",
                    "source_kind": source_kind, "evidence_level": "causal_validated",
                }]},
            },
        },
        "result": {"status": "completed"},
    }
    response = EvidenceGateway().for_warden_workflow(
        entry, artifacts=LocalArtifactStore(tmp_path / source_kind),
    )
    assert response["items"] == []
    assert any(source_kind in note for note in response["limitations"])


def test_unrecognized_success_alias_does_not_count_as_completed(tmp_path):
    entry = {
        "request_id": "workflow-status", "workflow_status": "unknown", "run_id": "run-status",
        "request": {"metadata": {}},
        "context": {
            "plan": {"plan_id": "plan-status"},
            "run": {"run_id": "run-status", "plan_id": "plan-status", "status": "success"},
        },
        "result": {"status": "success"},
    }
    response = EvidenceGateway().for_warden_workflow(
        entry, artifacts=LocalArtifactStore(tmp_path / "artifacts"),
    )
    assert response["run_status"] == "unknown"
    assert response["items"] == []


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
