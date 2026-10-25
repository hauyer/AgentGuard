"""H: End-to-end test for evaluation CLI persistence and API visibility.

Verifies that an evaluation written by the same storage conventions used by
the production API is discoverable through the read-only research endpoints.
"""

import json
import tempfile
from pathlib import Path

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
    assert registry.get("eval-dup-001") is not None
