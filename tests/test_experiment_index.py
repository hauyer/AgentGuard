"""Contract tests for the read-only experiment index API (C2)."""

from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def _run_engine_report(client: TestClient, app, **overrides) -> dict:
    scenario_id = overrides.pop(
        "scenario_id", app.state.engine.scenarios.list_scenarios()[0].scenario_id
    )
    payload = {
        "scenario_id": scenario_id,
        "experiment_id": overrides.pop("experiment_id", None) or f"exp-{len(client.app.state.artifacts.list_metadata())}",
        "model_version": overrides.pop("model_version", None),
        **overrides,
    }
    response = client.post("/v1/runs", json={k: v for k, v in payload.items() if v is not None})
    assert response.status_code == 200, response.text
    return response.json()


def _seed_workflow(app, request_id: str, *, status: str, context: dict, result: dict) -> None:
    store = app.state.warden_workflows
    store.begin(request_id, {
        "request_id": request_id, "contract_id": "contract-x", "model_id": "model-7b",
        "runtime_id": "runtime-x", "mode": "observation",
        "scenario_id": "sc-warden", "risk_type": "secret_leak",
    })
    store.update(request_id, status, context=context, result=result)


def _run_payload(status: str = "completed", metrics: dict | None = None) -> dict:
    return {
        "run_id": "run-w1", "plan_id": "plan-w1", "status": status,
        "evidence": {"findings": [], "artifacts": [], "metrics": metrics or {}},
    }


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #

def test_index_lists_workflows_and_engine_reports_with_provenance(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    run_payload = _run_payload(metrics={"trace_alignment.duration_seconds": 12.5})
    _seed_workflow(app, "inv-a", status="finished",
                   context={"run": run_payload}, result={"status": "completed", "run": run_payload})
    with TestClient(app) as client:
        report = _run_engine_report(client, app, experiment_id="exp-syn-1")
        listed = client.get("/v1/experiments").json()
    by_source = {item["data_source"]: item for item in listed["items"]}
    assert set(by_source) == {"warden_workflow", "synthetic_report"}
    warden = by_source["warden_workflow"]
    assert warden["request_id"] == "inv-a"
    assert warden["execution_status"] == "completed"
    assert warden["evidence_level"] == "observational"
    assert warden["metrics_available"] is True
    assert warden["experiment_id"] is None  # no stable grouping in the journal schema
    assert any("no stable experiment grouping" in note for note in listed["limitations"])
    synthetic = by_source["synthetic_report"]
    assert synthetic["run_id"] == report["run_id"]
    assert synthetic["experiment_id"] == "exp-syn-1"
    assert synthetic["evidence_level"] == "synthetic"
    assert synthetic["raw_status"] == "completed"


def test_index_pagination_status_and_scenario_filters(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    failed_payload = _run_payload(status="failed")
    _seed_workflow(app, "inv-failed", status="finished",
                   context={"run": failed_payload}, result={"status": "failed", "run": failed_payload})
    ok_payload = _run_payload()
    _seed_workflow(app, "inv-ok", status="finished",
                   context={"run": ok_payload}, result={"status": "completed", "run": ok_payload})
    with TestClient(app) as client:
        page = client.get("/v1/experiments", params={"limit": 1}).json()
        assert page["count"] == 1 and page["total"] == 2
        assert page["next_offset"] == 1
        failed = client.get("/v1/experiments", params={"status": "failed"}).json()
        assert [item["request_id"] for item in failed["items"]] == ["inv-failed"]
        assert failed["items"][0]["execution_status"] == "failed"
        assert failed["items"][0]["evidence_level"] == "unknown"
        warden_only = client.get("/v1/experiments", params={"scenario_id": "sc-warden"}).json()
        assert all(item["scenario_id"] == "sc-warden" for item in warden_only["items"])
        source_only = client.get(
            "/v1/experiments", params={"data_source": "synthetic_report"}
        ).json()
        assert source_only["items"] == []
        bad_source = client.get("/v1/experiments", params={"data_source": "other"})
        assert bad_source.status_code == 422


def test_index_sorted_by_created_at_descending(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        _run_engine_report(client, app, experiment_id="exp-older")
        _run_engine_report(client, app, experiment_id="exp-newer")
        listed = client.get("/v1/experiments", params={"data_source": "synthetic_report"}).json()
    created = [item["created_at"] for item in listed["items"]]
    assert created == sorted(created, reverse=True)


def test_index_survives_service_restart(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir)
    run_payload = _run_payload()
    _seed_workflow(app, "inv-restart", status="finished",
                   context={"run": run_payload}, result={"status": "completed", "run": run_payload})
    with TestClient(app) as client:
        _run_engine_report(client, app, experiment_id="exp-restart")
    restarted = create_app(data_dir=data_dir)
    with TestClient(restarted) as client:
        listed = client.get("/v1/experiments").json()
    assert {item["data_source"] for item in listed["items"]} == {"warden_workflow", "synthetic_report"}


def test_legacy_journal_rows_are_indexed(tmp_path):
    """Rows shaped like the pre-1.0 real observation journal still index correctly."""
    app = create_app(data_dir=tmp_path / "data")
    legacy_result = {"status": "completed", "run": _run_payload()}
    _seed_workflow(
        app, "live-observation-legacy-20261024",
        status="finished", context={"plan": {"plan_id": "plan-w1"}, "run": legacy_result["run"]},
        result=legacy_result,
    )
    with TestClient(app) as client:
        listed = client.get("/v1/experiments").json()
    entry = next(item for item in listed["items"] if item["request_id"] == "live-observation-legacy-20261024")
    assert entry["execution_status"] == "completed"
    assert entry["raw_status"] == "finished/completed"
    detail = client.get("/v1/experiments/live-observation-legacy-20261024").json()
    assert detail["metrics_available"] is False  # legacy payload carries no metrics dict
    assert any("no measured metrics" in note for note in detail["limitations"])


# --------------------------------------------------------------------------- #
# detail
# --------------------------------------------------------------------------- #

def test_detail_resolves_by_experiment_request_and_run_ids(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    run_payload = _run_payload(metrics={"trace_alignment.duration_seconds": 9.0,
                                        "trace_alignment.message_count": 4.0})
    _seed_workflow(app, "inv-detail", status="finished",
                   context={"run": run_payload}, result={"status": "completed", "run": run_payload})
    with TestClient(app) as client:
        by_request = client.get("/v1/experiments/inv-detail").json()
        by_run = client.get("/v1/experiments/run-w1").json()
        report = _run_engine_report(client, app, experiment_id="exp-detail")
        by_experiment = client.get("/v1/experiments/exp-detail").json()
        missing = client.get("/v1/experiments/exp-missing")
    assert by_request["request_id"] == "inv-detail"
    assert by_run["request_id"] == "inv-detail"
    assert by_request["metrics"]["source_metrics"] == {
        "trace_alignment.duration_seconds": 9.0, "trace_alignment.message_count": 4.0,
    }
    assert by_request["metrics"]["time_seconds"] == 9.0
    assert by_experiment["experiment_id"] == "exp-detail"
    assert by_experiment["run_id"] == report["run_id"]
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "EXPERIMENT_NOT_FOUND"


def test_engine_detail_reports_only_measured_metrics_and_stays_synthetic(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        report = _run_engine_report(client, app, experiment_id="exp-metrics")
        detail = client.get(f"/v1/experiments/{report['run_id']}").json()
    assert detail["data_source"] == "synthetic_report"
    assert detail["metrics_available"] is True
    metrics = detail["metrics"]
    assert metrics["benign_utility"] is not None
    assert metrics["risk_exposure"] is not None
    assert any("synthetic" in note.lower() for note in detail["limitations"])


def test_workflow_without_metrics_reports_null_not_zero(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    run_payload = _run_payload()  # metrics dict empty
    _seed_workflow(app, "inv-nometrics", status="finished",
                   context={"run": run_payload}, result={"status": "completed", "run": run_payload})
    with TestClient(app) as client:
        detail = client.get("/v1/experiments/inv-nometrics").json()
    assert detail["metrics_available"] is False
    assert detail["metrics"] is None
    assert any("no measured metrics" in note for note in detail["limitations"])


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #

def test_compare_matched_reports_is_descriptive_only(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        first = _run_engine_report(client, app, experiment_id="exp-c1", model_version="mock-1.0")
        second = _run_engine_report(client, app, experiment_id="exp-c2", model_version="mock-1.0")
        body = {"run_ids": [first["run_id"], second["run_id"]]}
        comparable = client.post("/v1/experiments/compare", json=body).json()
    assert comparable["comparable"] is True
    assert comparable["status"] == "comparable"
    names = {metric["name"] for metric in comparable["metrics"]}
    assert {"risk_exposure", "benign_utility"} <= names
    for metric in comparable["metrics"]:
        assert set(metric["values"]) == {first["run_id"], second["run_id"]}
        assert "difference" in metric
    assert any("cannot support a causal defense-effectiveness claim" in note
               for note in comparable["limitations"])
    assert any("No recorded treatment/control pairing" in note for note in comparable["limitations"])


def test_compare_rejects_mismatched_conditions_and_unknown_runs(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    scenarios = [s.scenario_id for s in app.state.engine.scenarios.list_scenarios()]
    with TestClient(app) as client:
        first = _run_engine_report(client, app, experiment_id="exp-m1", model_version="mock-1.0")
        other_model = _run_engine_report(client, app, experiment_id="exp-m2", model_version="mock-2.0")
        unrecorded = _run_engine_report(client, app, experiment_id="exp-m3")
        not_found = client.post(
            "/v1/experiments/compare", json={"run_ids": [first["run_id"], "run-none"]}
        ).json()
        model_mismatch = client.post(
            "/v1/experiments/compare",
            json={"run_ids": [first["run_id"], other_model["run_id"]]},
        ).json()
        unrecorded_model = client.post(
            "/v1/experiments/compare",
            json={"run_ids": [first["run_id"], unrecorded["run_id"]]},
        ).json()
    assert not_found["comparable"] is False
    assert "RUN_REPORT_NOT_FOUND:run-none" in not_found["reasons"]
    assert model_mismatch["comparable"] is False
    assert any(reason.startswith("MODEL_VERSION_MISMATCH") for reason in model_mismatch["reasons"])
    assert unrecorded_model["comparable"] is False
    assert "MODEL_VERSION_NOT_RECORDED" in unrecorded_model["reasons"]
    if len(scenarios) > 1:
        with TestClient(app) as client:
            other_scenario = _run_engine_report(
                client, app, experiment_id="exp-m4", model_version="mock-1.0",
                scenario_id=scenarios[1],
            )
            scenario_mismatch = client.post(
                "/v1/experiments/compare",
                json={"run_ids": [first["run_id"], other_scenario["run_id"]]},
            ).json()
        assert any(reason.startswith("SCENARIO_MISMATCH") for reason in scenario_mismatch["reasons"])


def test_compare_validates_request_shape(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        single = client.post("/v1/experiments/compare", json={"run_ids": ["run-a"]})
        triple = client.post(
            "/v1/experiments/compare",
            json={"run_ids": ["run-a", "run-b", "run-c"]},
        )
        duplicate = client.post(
            "/v1/experiments/compare", json={"run_ids": ["run-a", "run-a"]}
        )
    assert single.status_code == 422
    assert triple.status_code == 422
    assert duplicate.status_code == 422


def test_openapi_registers_experiment_routes(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        schema = client.get("/openapi.json").json()
    assert "get" in schema["paths"]["/v1/experiments"]
    assert "get" in schema["paths"]["/v1/experiments/{experiment_id}"]
    assert "post" in schema["paths"]["/v1/experiments/compare"]
