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
    assert all(item.source_type == "synthetic" for item in items)
    assert all(item.run_id == report.run_id for item in items)
    assert all(item.experiment_id == report.experiment_id for item in items)
    assert all(item.model_id == item.data_version == "unknown" for item in items)
    assert all(item.production_phase == "unknown" for item in items)
    assert all(
        item.artifact_hashes.get(report_artifact.artifact_ref.artifact_id)
        == report_artifact.artifact_ref.sha256
        for item in items
    )
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
            "metadata": {
                "scenario_version": "v2", "model_revision": "local-7b@rev4",
                "dataset_version": "dataset-v3", "experiment_id": "exp-warden-1",
            },
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
                        "production_phase": "intervention",
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
    assert item["source_type"] == "warden_api"
    assert item["run_id"] == "run-1"
    assert item["experiment_id"] == "exp-warden-1"
    assert item["production_phase"] == "intervention"
    assert item["model_id"] == "local-7b"
    assert item["model_version"] == "local-7b@rev4"
    assert item["data_version"] == "dataset-v3"
    assert item["artifact_hashes"] == {}
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


def _complete_causal_validation_payload():
    names = [
        "preregistered_design", "matched_control", "sham_control", "negative_control",
        "unrelated_position_control", "randomization_baseline",
        "utility", "control_output", "treatment_output", "sham_output",
        "negative_control_output", "unrelated_position_output", "execution_log",
        "intervention_parameters",
        "evaluator", "statistical_report", "alternatives_review", "methodology_review",
    ]
    digests = {name: f"{index:064x}" for index, name in enumerate(names, start=1)}
    ref = lambda name: f"artifact://sha256/{digests[name]}"
    return {
        "independent_repetitions": 2,
        "independent_run_ids": ["run-a", "run-b"],
        "sample_ids": ["sample-a", "sample-b"],
        "independent_case_family_ids": ["family-a", "family-b"],
        "experiment_config_sha256": "c" * 64,
        "preregistered_design_artifact_ref": ref("preregistered_design"),
        "preregistered_design_sha256": digests["preregistered_design"],
        "control_condition": "secret_off",
        "treatment_condition": "secret_on",
        "matched_control_passed": True,
        "matched_control_artifact_ref": ref("matched_control"),
        "sham_control_passed": True,
        "sham_control_artifact_ref": ref("sham_control"),
        "negative_control_passed": True,
        "negative_control_artifact_ref": ref("negative_control"),
        "unrelated_position_control_passed": True,
        "unrelated_position_control_artifact_ref": ref("unrelated_position_control"),
        "randomization_or_permutation_baseline": "seeded sign-flip permutation test",
        "randomization_or_permutation_baseline_artifact_ref": ref("randomization_baseline"),
        "benign_utility_measured": True,
        "utility_artifact_ref": ref("utility"),
        "control_output_artifact_ref": ref("control_output"),
        "treatment_output_artifact_ref": ref("treatment_output"),
        "sham_output_artifact_ref": ref("sham_output"),
        "negative_control_output_artifact_ref": ref("negative_control_output"),
        "unrelated_position_output_artifact_ref": ref("unrelated_position_output"),
        "execution_log_artifact_ref": ref("execution_log"),
        "execution_log_sha256": digests["execution_log"],
        "intervention_parameters_artifact_ref": ref("intervention_parameters"),
        "intervention_parameters_sha256": digests["intervention_parameters"],
        "evaluator_version": "evaluator-1.2.0",
        "evaluator_artifact_ref": ref("evaluator"),
        "primary_outcome_metric": "secret_recovery_rate",
        "effect_size": -0.2,
        "confidence_interval_lower": -0.4,
        "confidence_interval_upper": -0.1,
        "multiple_comparison_method": "holm",
        "alpha": 0.05,
        "statistical_report_artifact_ref": ref("statistical_report"),
        "artifact_hashes_verified": True,
        "alternative_explanations_reviewed": True,
        "alternative_explanations_review_ref": ref("alternatives_review"),
        "methodology_review_record_ref": ref("methodology_review"),
        "methodology_reviewer_id": "reviewer-1",
    }


def test_causal_validation_requires_sham_and_content_addressed_artifacts():
    payload = _complete_causal_validation_payload()
    assert CausalValidationCriteria.model_validate(payload).sham_control_passed

    missing_sham = {**payload, "sham_control_passed": False}
    with pytest.raises(ValidationError, match="every methodological check"):
        CausalValidationCriteria.model_validate(missing_sham)

    mismatched_hash = {**payload, "execution_log_sha256": "f" * 64}
    with pytest.raises(ValidationError, match="does not match"):
        CausalValidationCriteria.model_validate(mismatched_hash)


def test_causal_evidence_must_expose_every_reviewed_artifact_reference():
    criteria = CausalValidationCriteria.model_validate(_complete_causal_validation_payload())
    with pytest.raises(ValidationError, match="every validation artifact reference"):
        GovernedEvidence(
            evidence_id="ev-causal",
            source_system="AgentGuard",
            evidence_type="evaluation",
            evidence_level="causal_validated",
            verification_status="verified",
            content_integrity_status="verified",
            source_authenticity_status="verified",
            methodology_review_status="verified",
            run_status="completed",
            artifact_refs=[criteria.preregistered_design_artifact_ref],
            causal_validation=criteria,
        )


def test_verified_defense_artifact_requires_sham_and_unrelated_position_controls(tmp_path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    arrays = {"control": [0.8, 0.7], "treatment": [0.4, 0.3]}
    payload = {
        "run_id": "run-defense",
        "candidate_id": "candidate-defense",
        **arrays,
        "matched_control_passed": True,
        "sham_control_passed": True,
        "negative_control_passed": True,
        "unrelated_position_control_passed": True,
        "benign_utility_measured": True,
        "risk_transfer_checked": True,
        "rollback_tested": True,
        "independent_repetitions": 2,
    }
    artifact = artifacts.put(
        json.dumps(payload).encode(),
        metadata={
            "run_id": "run-defense",
            "candidate_id": "candidate-defense",
            "evidence_kind": "paired_defense_verification",
        },
    )
    gateway = EvidenceGateway()
    verified = gateway.verify_defense_artifacts(
        [artifact.artifact_ref.uri], run_id="run-defense",
        candidate_id="candidate-defense", artifacts=artifacts, arrays=arrays,
    )
    assert verified == [artifact.artifact_ref.artifact_id]

    payload["sham_control_passed"] = False
    missing_sham_artifact = artifacts.put(
        json.dumps(payload).encode(),
        metadata={
            "run_id": "run-defense",
            "candidate_id": "candidate-defense",
            "evidence_kind": "paired_defense_verification",
        },
    )
    rejected = gateway.verify_defense_artifacts(
        [missing_sham_artifact.artifact_ref.uri], run_id="run-defense",
        candidate_id="candidate-defense", artifacts=artifacts, arrays=arrays,
    )
    assert rejected == []


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
