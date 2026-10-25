"""End-to-end persistence checks using synthetic journal data (no live model).

Runs the actual run_investigation_eval.py script as a subprocess, then
verifies that the resulting evaluation is discoverable through the public API.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.services.warden_workflow_store import WardenWorkflowStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cli_to_api_evaluation_persistence(tmp_path):
    """End-to-end: run CLI, then query evaluation via API."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # 1. Create a legal base investigation record in the shared store
    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    request_id = "e2e-base-request-001"
    workflows.begin(request_id, {
        "request_id": request_id,
        "contract_id": "test-contract",
        "model_id": "test-model",
        "runtime_id": "test-runtime",
        "mode": "observation",
        "scenario_id": "test-scenario",
        "metadata": {"source_kind": "synthetic"},
    })
    workflows.update(
        request_id, "finished",
        context={"run": {"run_id": "run-001", "status": "completed"}},
        result={"status": "completed", "run": {"run_id": "run-001"}},
    )

    # 2. Create dataset manifest
    manifest = {
        "schema_version": "1.0",
        "dataset_name": "e2e-test",
        "data_partition": "test",
        "cases": [
            {
                "case_id": "case-001",
                "base_request_id": request_id,
                "source_kind": "synthetic",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # 3. Run the actual CLI as a subprocess
    report_path = tmp_path / "report.md"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_investigation_eval.py"),
            "--data-dir", str(data_dir),
            "--dataset-manifest", str(manifest_path),
            "--strategies", "fixed_order",
            "--repetitions", "1",
            "--partition", "test",
            "--out", str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=60,
    )

    # CLI should exit with code 0
    assert result.returncode == 0, f"CLI failed: {result.stderr}"

    # Report file should exist
    assert report_path.exists()
    report_content = report_path.read_text(encoding="utf-8")
    assert "comparison" in report_content.lower() or "evaluation" in report_content.lower()
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert manifest_sha256 in report_content
    assert "source_kind values: synthetic" in report_content
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT), check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    assert f"source_commit：`{source_revision}`" in report_content

    # 4. Create a fresh API app with the same data_dir
    app = create_app(data_dir=data_dir)

    # 5. Query the evaluation list
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1, f"Expected one CLI evaluation: {body}"

    # 6. Query the evaluation created by the CLI
    index_entry = body["items"][0]
    evaluation_id = index_entry["evaluation_id"]
    with TestClient(app) as client:
        resp = client.get(f"/v1/research/strategy-evaluations/{evaluation_id}")
    assert resp.status_code == 200
    detail = resp.json()

    # Read the exact content-addressed artifact produced by this CLI execution.
    artifact_id = index_entry["report_artifact_id"]
    assert artifact_id in report_content
    report_payload = json.loads(
        LocalArtifactStore(data_dir / "artifacts").get_bytes(artifact_id)
    )

    # The index, API detail, and artifact must all refer to this one execution.
    assert evaluation_id == f"eval-{report_payload['comparison_id']}"
    assert detail["comparison_id"] == report_payload["comparison_id"]
    assert detail["data_partition"] == report_payload["data_partition"] == "test"
    assert detail["source_commit"] == report_payload["source_commit"]
    assert re.fullmatch(r"[0-9a-f]{40}", report_payload["source_commit"])
    assert any(
        manifest_sha256 in limitation and "source_kind values: synthetic" in limitation
        for limitation in report_payload["limitations"]
    )
    assert detail["strategies"] == report_payload["strategies"] == ["fixed_order"]
    assert detail["repetitions"] == report_payload["repetitions"] == 1
    assert detail["runs"] == report_payload["runs"]
    assert detail["aggregates"] == report_payload["aggregates"]
    assert report_payload["base_request_ids"] == [request_id]
    assert len(detail["runs"]) == 1
    assert detail["runs"][0]["strategy"] == "fixed_order"
    assert detail["runs"][0]["base_request_id"] == request_id
    assert detail["runs"][0]["repetition"] == 1
    assert detail["report_artifact_id"] == artifact_id
    assert index_entry["evaluation_id"] == evaluation_id
    assert index_entry["comparison_id"] == report_payload["comparison_id"]
    assert index_entry["data_partition"] == report_payload["data_partition"]
    assert index_entry["source_commit"] == report_payload["source_commit"]
    assert index_entry["report_artifact_id"] == artifact_id
    assert manifest["cases"][0]["source_kind"] == "synthetic"


def test_cli_no_input_returns_error(tmp_path):
    """CLI should exit with error when no valid base requests are provided."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    manifest = {
        "schema_version": "1.0",
        "dataset_name": "e2e-test",
        "data_partition": "test",
        "cases": [
            {
                "case_id": "case-001",
                "base_request_id": "nonexistent-request",
                "source_kind": "synthetic",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_investigation_eval.py"),
            "--data-dir", str(data_dir),
            "--dataset-manifest", str(manifest_path),
            "--strategies", "fixed_order",
            "--repetitions", "1",
            "--out", str(tmp_path / "report.md"),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )

    # CLI should exit with non-zero code when no valid base requests
    assert result.returncode != 0
    assert "MISSING" in result.stdout
    assert not (tmp_path / "report.md").exists()


def test_cli_rejects_missing_and_malformed_manifests(tmp_path):
    """Missing and invalid JSON manifests fail before an evaluation is written."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report_path = tmp_path / "report.md"
    missing_manifest = tmp_path / "missing.json"
    malformed_manifest = tmp_path / "malformed.json"
    malformed_manifest.write_text("{not valid json", encoding="utf-8")

    for manifest_path in (missing_manifest, malformed_manifest):
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_investigation_eval.py"),
                "--data-dir", str(data_dir),
                "--dataset-manifest", str(manifest_path),
                "--strategies", "fixed_order",
                "--repetitions", "1",
                "--partition", "test",
                "--out", str(report_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode != 0
        assert not report_path.exists()


def test_cli_rejects_incomplete_base_investigation(tmp_path):
    """A journal record that has not completed is not used as a test case."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    request_id = "e2e-incomplete-base"
    workflows.begin(request_id, {
        "request_id": request_id,
        "contract_id": "test-contract",
        "model_id": "test-model",
        "runtime_id": "test-runtime",
        "mode": "observation",
        "scenario_id": "synthetic-test-scenario",
    })
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "dataset_name": "synthetic-incomplete-case",
        "data_partition": "test",
        "cases": [{
            "case_id": "case-incomplete",
            "base_request_id": request_id,
            "source_kind": "synthetic",
        }],
    }), encoding="utf-8")
    report_path = tmp_path / "report.md"

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_investigation_eval.py"),
            "--data-dir", str(data_dir),
            "--dataset-manifest", str(manifest_path),
            "--strategies", "fixed_order",
            "--repetitions", "1",
            "--partition", "test",
            "--out", str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )

    assert result.returncode == 3
    assert "INCOMPLETE" in result.stdout
    assert not report_path.exists()


def test_cli_rejects_unusable_data_directory(tmp_path):
    """A file where the persistent data directory should be fails closed."""
    data_dir = tmp_path / "not-a-directory"
    data_dir.write_text("occupied", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    report_path = tmp_path / "report.md"

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_investigation_eval.py"),
            "--data-dir", str(data_dir),
            "--dataset-manifest", str(manifest_path),
            "--strategies", "fixed_order",
            "--repetitions", "1",
            "--out", str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )

    assert result.returncode != 0
    assert not report_path.exists()
