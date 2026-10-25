"""H: End-to-end test for evaluation CLI persistence and API visibility.

Verifies that an evaluation written by the same storage conventions used by
the production API is discoverable through the read-only research endpoints.
"""

import json

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.services.evaluation_registry import EvaluationRegistry
from agentguard.services.warden_workflow_store import WardenWorkflowStore


def test_e2e_evaluation_written_to_shared_store_is_visible_via_api(tmp_path):
    """End-to-end: register evaluation in shared store, then query via API."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Use the same filenames as the production API
    workflows_path = data_dir / "warden_workflows.sqlite3"
    artifacts_dir = data_dir / "artifacts"

    # 1. Simulate CLI having written an evaluation to the shared store
    workflows = WardenWorkflowStore(workflows_path)
    artifacts = LocalArtifactStore(artifacts_dir)
    registry = EvaluationRegistry(workflows)

    # Write a real artifact
    payload = json.dumps({
        "comparison_id": "comparison-e2e-001",
        "strategies": ["fixed_order", "risk_score"],
        "repetitions": 1,
        "runs": [],
        "aggregates": [],
        "data_partition": "held_out_family",
        "source_commit": "abc123",
        "executed_at": "2026-10-25T00:00:00+00:00",
    }).encode("utf-8")
    artifact = artifacts.put(payload, name="e2e-report.json")

    # Register in the evaluation index
    registry.register(
        evaluation_id="eval-e2e-001",
        comparison_id="comparison-e2e-001",
        report_artifact_id=artifact.artifact_ref.artifact_id,
        data_partition="held_out_family",
        source_commit="abc123",
        executed_at="2026-10-25T00:00:00+00:00",
    )

    # 2. Create the API app with the same data_dir
    app = create_app(data_dir=data_dir)

    # 3. Query the evaluation list
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["items"][0]["evaluation_id"] == "eval-e2e-001"
    assert body["items"][0]["data_partition"] == "held_out_family"

    # 4. Query the evaluation detail
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations/eval-e2e-001")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["comparison_id"] == "comparison-e2e-001"
    assert detail["data_partition"] == "held_out_family"
    assert detail["source_commit"] == "abc123"


def test_e2e_empty_directory_returns_empty_list(tmp_path):
    """Empty data directory returns empty evaluation list."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    app = create_app(data_dir=data_dir)
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_e2e_missing_artifact_returns_410(tmp_path):
    """Index points to missing artifact returns 410 Gone."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    registry = EvaluationRegistry(workflows)

    # Register an evaluation pointing to a non-existent artifact
    registry.register(
        evaluation_id="eval-missing-001",
        comparison_id="comparison-missing",
        report_artifact_id="art-does-not-exist",
        data_partition="held_out_family",
        source_commit="abc123",
        executed_at="2026-10-25T00:00:00+00:00",
    )

    app = create_app(data_dir=data_dir)
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations/eval-missing-001")
    assert resp.status_code == 410


def test_e2e_duplicate_id_same_content_is_idempotent(tmp_path):
    """Same evaluation ID with same content can be registered twice."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    registry = EvaluationRegistry(workflows)

    kwargs = {
        "evaluation_id": "eval-dup-001",
        "comparison_id": "comparison-dup",
        "report_artifact_id": "art-xxx",
        "data_partition": "held_out_family",
        "source_commit": "abc123",
        "executed_at": "2026-10-25T00:00:00+00:00",
    }
    registry.register(**kwargs)
    # Second registration with same content should not raise
    registry.register(**kwargs)
    assert registry.get("eval-dup-001") == {
        "evaluation_id": "eval-dup-001",
        "comparison_id": "comparison-dup",
        "report_artifact_id": "art-xxx",
        "data_partition": "held_out_family",
        "source_commit": "abc123",
        "executed_at": "2026-10-25T00:00:00+00:00",
        "status": "available",
    }
    assert registry.list(limit=10) == [registry.get("eval-dup-001")]


def test_i2_detail_response_includes_index_artifact_id(tmp_path):
    """I2: Detail response must include the artifact_id from the index."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    registry = EvaluationRegistry(workflows)

    # Write a report artifact where report_artifact_id is empty
    # (simulating the original serialization order issue)
    report_payload = json.dumps({
        "comparison_id": "comparison-i2-001",
        "report_artifact_id": "",  # empty before assignment
        "data_partition": "test",
        "source_commit": "abc123",
        "executed_at": "2026-10-25T00:00:00+00:00",
        "runs": [],
        "aggregates": [],
    }).encode("utf-8")
    artifact = artifacts.put(report_payload, name="i2-report.json")

    # Register with the actual artifact_id
    registry.register(
        evaluation_id="eval-i2-001",
        comparison_id="comparison-i2-001",
        report_artifact_id=artifact.artifact_ref.artifact_id,
        data_partition="test",
        source_commit="abc123",
        executed_at="2026-10-25T00:00:00+00:00",
    )

    app = create_app(data_dir=data_dir)
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations/eval-i2-001")
    assert resp.status_code == 200
    detail = resp.json()
    # The returned report_artifact_id must match the index, not the empty string
    assert detail["report_artifact_id"] == artifact.artifact_ref.artifact_id


def test_i2_corrupted_artifact_returns_410(tmp_path):
    """I2: Corrupted artifact returns 410 Gone."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    registry = EvaluationRegistry(workflows)

    # Write a corrupted artifact
    corrupted_payload = b"{ this is not valid json"
    artifact = artifacts.put(corrupted_payload, name="corrupted.json")

    registry.register(
        evaluation_id="eval-i2-corrupt",
        comparison_id="comparison-corrupt",
        report_artifact_id=artifact.artifact_ref.artifact_id,
        data_partition="test",
        source_commit="abc123",
        executed_at="2026-10-25T00:00:00+00:00",
    )

    app = create_app(data_dir=data_dir)
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations/eval-i2-corrupt")
    assert resp.status_code == 410
    assert resp.json()["detail"]["error"] == "EVALUATION_ARTIFACT_UNAVAILABLE"


def test_i2_same_id_different_content_raises_conflict(tmp_path):
    """I2: Same evaluation ID with different content raises conflict."""
    import pytest

    from agentguard.services.evaluation_registry import EvaluationRegistryError

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    registry = EvaluationRegistry(workflows)

    registry.register(
        evaluation_id="eval-i2-conflict",
        comparison_id="comparison-a",
        report_artifact_id="art-aaa",
        data_partition="test",
        source_commit="abc123",
        executed_at="2026-10-25T00:00:00+00:00",
    )
    with pytest.raises(EvaluationRegistryError, match="already registered"):
        registry.register(
            evaluation_id="eval-i2-conflict",
            comparison_id="comparison-b",
            report_artifact_id="art-bbb",
            data_partition="test",
            source_commit="def456",
            executed_at="2026-10-25T01:00:00+00:00",
        )
