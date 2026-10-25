"""I1: End-to-end test for CLI -> persistence -> API evaluation visibility.

Runs the actual run_investigation_eval.py script as a subprocess, then
verifies that the resulting evaluation is discoverable through the public API.
"""

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
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

    # 4. Create a fresh API app with the same data_dir
    app = create_app(data_dir=data_dir)

    # 5. Query the evaluation list
    with TestClient(app) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1, f"No evaluations found: {body}"

    # 6. Query the first evaluation detail
    evaluation_id = body["items"][0]["evaluation_id"]
    with TestClient(app) as client:
        resp = client.get(f"/v1/research/strategy-evaluations/{evaluation_id}")
    assert resp.status_code == 200
    detail = resp.json()

    # Verify all required fields are present and consistent
    assert detail["data_partition"] == "test"
    assert "comparison_id" in detail
    assert "strategies" in detail
    assert "repetitions" in detail
    assert "runs" in detail
    assert "aggregates" in detail
    assert "source_commit" in detail
    assert "report_artifact_id" in detail

    # Verify the evaluation_id matches what we expect
    assert evaluation_id.startswith("eval-")
    assert detail["comparison_id"] == evaluation_id.removeprefix("eval-")

    # Verify runs count matches repetitions
    assert len(detail["runs"]) >= 1


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
