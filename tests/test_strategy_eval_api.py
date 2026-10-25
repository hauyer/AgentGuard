"""G1: Strategy evaluation public API tests."""


from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def _app(tmp_path):
    return create_app(data_dir=tmp_path / "data")


def test_empty_evaluation_list(tmp_path):
    """Empty list returns 200 with empty items."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["count"] == 0
    assert body["schema_version"] == "1.0"


def test_evaluation_not_found(tmp_path):
    """Unknown evaluation ID returns 404."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/v1/research/strategy-evaluations/eval-nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "EVALUATION_NOT_FOUND"


def test_evaluation_list_pagination(tmp_path):
    """Pagination parameters are accepted."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/v1/research/strategy-evaluations", params={
            "limit": 10, "offset": 0,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 10
    assert body["offset"] == 0


def test_evaluation_bad_limit_rejected(tmp_path):
    """Invalid limit returns 422."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/v1/research/strategy-evaluations", params={"limit": 9999})
    assert resp.status_code == 422


def test_evaluation_partition_filter(tmp_path):
    """Partition filter parameter is accepted."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/v1/research/strategy-evaluations", params={
            "data_partition": "held_out_family",
        })
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_openapi_registers_evaluation_routes(tmp_path):
    """New routes appear in OpenAPI schema."""
    with TestClient(_app(tmp_path)) as client:
        schema = client.get("/openapi.json").json()
    assert "/v1/research/strategy-evaluations" in schema["paths"]
    assert "/v1/research/strategy-evaluations/{evaluation_id}" in schema["paths"]
    assert "get" in schema["paths"]["/v1/research/strategy-evaluations"]
    assert "get" in schema["paths"]["/v1/research/strategy-evaluations/{evaluation_id}"]


def test_evaluation_list_does_not_trigger_new_experiment(tmp_path):
    """GET endpoints must not trigger new experiments."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    # No POST / run should have happened
    assert resp.json()["count"] == 0


def test_evaluation_registry_persists_across_restart(tmp_path):
    """Registered evaluation survives app restart."""
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir)
    # Manually insert a fake evaluation entry
    with app.state.evaluation_registry.workflows._connect() as db:
        db.execute(
            "INSERT INTO strategy_evaluations (evaluation_id, comparison_id, "
            "report_artifact_id, data_partition, source_commit, executed_at, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("eval-test-001", "comparison-test", "art-xxxx", "held_out_family",
             "abc123", "2026-10-25T00:00:00+00:00", "available",
             "2026-10-25T00:00:00+00:00"),
        )
    # Restart and query
    restarted = create_app(data_dir=data_dir)
    with TestClient(restarted) as client:
        resp = client.get("/v1/research/strategy-evaluations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["items"][0]["evaluation_id"] == "eval-test-001"
