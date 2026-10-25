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


def test_h2_register_same_id_same_content_is_idempotent(tmp_path):
    """H2: Registering same ID with same content is idempotent."""
    from agentguard.services.evaluation_registry import EvaluationRegistry
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    workflows = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    registry = EvaluationRegistry(workflows)
    kwargs = {
        "evaluation_id": "eval-h2-001",
        "comparison_id": "comparison-h2",
        "report_artifact_id": "art-xxxx",
        "data_partition": "held_out_family",
        "source_commit": "abc123",
        "executed_at": "2026-10-25T00:00:00+00:00",
    }
    registry.register(**kwargs)
    # Second registration with same content should not raise
    registry.register(**kwargs)
    assert registry.get("eval-h2-001") is not None


def test_h2_register_same_id_different_content_raises_conflict(tmp_path):
    """H2: Registering same ID with different content raises conflict error."""
    import pytest

    from agentguard.services.evaluation_registry import (
        EvaluationRegistry,
        EvaluationRegistryError,
    )
    from agentguard.services.warden_workflow_store import WardenWorkflowStore

    workflows = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    registry = EvaluationRegistry(workflows)
    registry.register(
        evaluation_id="eval-h2-002",
        comparison_id="comparison-a",
        report_artifact_id="art-aaa",
        data_partition="held_out_family",
        source_commit="abc123",
        executed_at="2026-10-25T00:00:00+00:00",
    )
    # Second registration with same ID but different content should raise
    with pytest.raises(EvaluationRegistryError, match="already registered"):
        registry.register(
            evaluation_id="eval-h2-002",
            comparison_id="comparison-b",
            report_artifact_id="art-bbb",
            data_partition="held_out_family",
            source_commit="def456",
            executed_at="2026-10-25T01:00:00+00:00",
        )
