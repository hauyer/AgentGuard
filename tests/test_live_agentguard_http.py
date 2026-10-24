"""Live AgentGuard HTTP integration for the C1-C3 public endpoints.

Starts the real uvicorn service as a subprocess (with its middleware stack,
auth, audit journal and SQLite stores) and exercises the new public routes
over plain HTTP — not through FastAPI's TestClient.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, method: str, path: str, body: dict | None = None):
    url = base + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    request.add_header("X-Request-ID", "live-acceptance-c0c3")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, response.headers
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload, exc.headers


@pytest.fixture(scope="module")
def live_service(tmp_path_factory):
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("live-data")
    env = {
        **os.environ,
        "AGENTGUARD_DATA_DIR": str(data_dir),
        "AGENTGUARD_WARDEN_URL": "",
        "AGENTGUARD_API_TOKEN": "",
        "AGENTGUARD_LLM_INVESTIGATOR": "",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentguard.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    ready = False
    while time.monotonic() < deadline:
        try:
            status, _, _ = _request(base, "GET", "/v1/system/health")
            ready = status == 200
            if ready:
                break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    if not ready:
        process.terminate()
        process.wait(timeout=10)
        pytest.fail("AgentGuard live service did not become ready")
    yield base
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def test_live_c0c3_endpoint_sequence(live_service):
    base = live_service

    # Readiness reports unconfigured WARDEN honestly (QWB-INT-007 answer).
    status, body, headers = _request(base, "GET", "/v1/system/readiness")
    assert status == 200
    assert body["schema_version"] == "1.0"
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["agentguard_api"]["status"] == "available"
    assert checks["warden_connectivity"]["status"] == "unavailable"
    assert checks["intervention_execution"]["status"] == "unsupported"
    assert checks["evidence_access"]["status"] == "blocked"
    assert headers.get("X-Request-ID") == "live-acceptance-c0c3"

    # Options: form sees nothing submittable without a kernel.
    status, options, _ = _request(base, "GET", "/v1/investigations/options")
    assert status == 200
    assert options["available"] is False
    assert options["error_code"] == "WARDEN_URL_NOT_CONFIGURED"
    assert all(mode["submittable"] is False for mode in options["modes"])

    # Experiment index starts empty; compare refuses unknown runs.
    status, listed, _ = _request(base, "GET", "/v1/experiments")
    assert status == 200 and listed["total"] == 0
    status, compare, _ = _request(
        base, "POST", "/v1/experiments/compare",
        {"run_ids": ["run-none-a", "run-none-b"]},
    )
    assert status == 200 and compare["comparable"] is False

    # Create a synthetic engine run and see it indexed with provenance.
    status, scenarios, _ = _request(base, "GET", "/v1/scenarios")
    assert status == 200 and scenarios["items"]
    scenario_id = scenarios["items"][0]["scenario_id"]
    status, report, _ = _request(base, "POST", "/v1/runs", {
        "scenario_id": scenario_id,
        "experiment_id": "exp-live-1",
        "model_version": "mock-live-1",
    })
    assert status == 200, report
    run_id = report["run_id"]
    status, listed, _ = _request(base, "GET", "/v1/experiments")
    entry = next(item for item in listed["items"] if item["run_id"] == run_id)
    assert entry["data_source"] == "synthetic_report"
    assert entry["experiment_id"] == "exp-live-1"

    # Graph: unknown workflow 404s; explicit synthetic run renders honestly.
    status, missing, _ = _request(base, "GET", "/v1/investigations/inv-none/interaction-graph")
    assert status == 404 and missing["detail"]["error"] == "WORKFLOW_NOT_FOUND"
    status, graph, _ = _request(
        base, "GET", f"/v1/investigations/inv-none/interaction-graph?run_id={run_id}"
    )
    assert status == 200
    assert graph["source"] == "agentguard_synthetic_report"
    assert graph["available"] is True
    assert any("synthetic" in note.lower() for note in graph["limitations"])
