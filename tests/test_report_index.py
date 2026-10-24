"""Contract tests for evidence capability fields and the report index (C5)."""

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.services.evidence_gateway import EvidenceGateway


def _workflow_entry(run_id: str, artifact_refs: list[str]) -> dict:
    return {
        "request_id": "inv-c5",
        "workflow_status": "completed",
        "run_id": run_id,
        "request": {"scenario_id": "sc-c5", "risk_type": "secret_flow", "model_id": "m"},
        "context": {
            "plan": {"plan_id": "plan-c5"},
            "run": {
                "run_id": run_id, "plan_id": "plan-c5", "status": "completed",
                "evidence_bundle": {"items": [{
                    "evidence_id": "ev-c5-1", "type": "observation",
                    "target_type": "message", "target_ref": "msg-c5",
                    "artifact_refs": artifact_refs,
                }]},
            },
        },
        "result": {"status": "completed"},
    }


def test_local_verified_artifact_grants_content_access_capability(tmp_path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    artifact = artifacts.put(
        b'{"observation": "message metadata"}',
        metadata={"run_id": "run-c5"}, tags=["warden-observation"],
    )
    ref = f"artifact://sha256/{artifact.artifact_ref.artifact_id}"
    response = EvidenceGateway().for_warden_workflow(
        _workflow_entry("run-c5", [ref]), artifacts=artifacts,
    )
    item = response["items"][0]
    assert item["metadata_available"] is True
    assert item["content_access_supported"] is True
    assert item["integrity_verified"] is True
    assert item["access_restriction"] == "local_verified_read"


def test_external_only_refs_never_gain_integrity_or_access(tmp_path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    response = EvidenceGateway().for_warden_workflow(
        _workflow_entry("run-c5", ["artifact://warden/ref-1"]),
        artifacts=artifacts,
    )
    item = response["items"][0]
    assert item["artifact_refs"] == ["artifact://warden/ref-1"]
    assert item["content_access_supported"] is False
    assert item["integrity_verified"] is False
    assert item["access_restriction"] == "read_only_reference"
    assert any("not independently checked" in note for note in item["limitations"])


def test_report_evidence_defaults_keep_reference_only_semantics(tmp_path):
    from agentguard.adapters.mock import MockBackend
    from agentguard.contracts.experiment import ExperimentRequest
    from agentguard.core.orchestrator import AgentGuardEngine

    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security", auto_intervene=False,
    ))
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    items = EvidenceGateway().for_report(
        report, report_artifact_id=None, artifacts=artifacts,
    )
    assert items
    for item in items:
        assert item.metadata_available is True
        assert item.content_access_supported is False
        assert item.integrity_verified is False
        assert item.access_restriction == "read_only_reference"


# --------------------------------------------------------------------------- #
# report index API
# --------------------------------------------------------------------------- #

def test_report_index_lists_persists_and_filters(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    with TestClient(app) as client:
        first = client.post("/v1/runs", json={
            "scenario_id": scenario, "experiment_id": "exp-r1",
        }).json()
        second = client.post("/v1/runs", json={
            "scenario_id": scenario, "experiment_id": "exp-r2",
        }).json()
        listed = client.get("/v1/reports").json()
        assert listed["schema_version"] == "1.0"
        assert listed["total"] == 2
        run_ids = {item["run_id"] for item in listed["items"]}
        assert run_ids == {first["run_id"], second["run_id"]}
        entry = listed["items"][0]
        assert set(entry) == {
            "run_id", "experiment_id", "scenario_id", "status",
            "backend_mode", "artifact_id", "created_at",
        }
        assert len(entry["artifact_id"]) == 64  # digest, never a path
        assert "file" not in entry["artifact_id"].lower()
        filtered = client.get(
            "/v1/reports", params={"scenario_id": "sc-not-exist"}
        ).json()
        assert filtered["total"] == 0
        page = client.get("/v1/reports", params={"limit": 1}).json()
        assert page["count"] == 1 and page["next_offset"] == 1


def test_report_read_survives_service_restart_via_artifact(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir)
    scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    with TestClient(app) as client:
        report = client.post("/v1/runs", json={
            "scenario_id": scenario, "experiment_id": "exp-revive",
        }).json()
    restarted = create_app(data_dir=data_dir)
    with TestClient(restarted) as client:
        fetched = client.get(f"/v1/reports/{report['run_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["run_id"] == report["run_id"]
        assert fetched.json()["experiment_id"] == "exp-revive"
        still_missing = client.get("/v1/reports/run-never-existed")
        assert still_missing.status_code == 404


def test_report_index_openapi_route_registered(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        schema = client.get("/openapi.json").json()
    assert "get" in schema["paths"]["/v1/reports"]
    assert "get" in schema["paths"]["/v1/reports/{run_id}"]
