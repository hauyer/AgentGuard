"""The pinned QWB-App operations remain byte-shape compatible at runtime."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.api.app import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "qwb_app_supported_operations_rc2.json"
RC5_SNAPSHOT = Path(__file__).resolve().parents[1] / "docs" / "integration" / "openapi-1.1.0rc5.json"


def test_runtime_openapi_preserves_all_pinned_qwb_app_operations(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        current = client.get("/openapi.json").json()

    for endpoint, expected_operation in fixture["operations"].items():
        method, path = endpoint.split(" ", maxsplit=1)
        assert path in current["paths"], f"removed pinned path: {endpoint}"
        actual_operation = current["paths"][path].get(method.lower())
        assert actual_operation is not None, f"removed pinned operation: {endpoint}"
        assert actual_operation == expected_operation, f"pinned operation changed: {endpoint}"


def test_rc5_snapshot_matches_runtime_and_documents_research_write_gates(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        current = client.get("/openapi.json").json()
    snapshot = json.loads(RC5_SNAPSHOT.read_text(encoding="utf-8"))
    assert current == snapshot
    assert current["info"]["version"] == "1.1.0rc5"
    assert len(current["paths"]) == 87
    assert sum(
        1 for path_item in current["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "responses" in operation
    ) == 99
    capability = current["paths"]["/v1/research/capabilities"]
    assert set(capability) == {"get"}
    for path in ("/v1/studies", "/v1/studies/{study_id}/analyses"):
        operation = current["paths"][path]["post"]
        header_names = {item["name"] for item in operation["parameters"] if item["in"] == "header"}
        assert {"X-AgentGuard-Token", "X-AgentGuard-Operator-Token", "X-Approval-Token"} <= header_names
        assert "Idempotency-Key" in header_names
        assert {"401", "403"} <= set(operation["responses"])
        assert "409" in operation["responses"]
        assert "413" in operation["responses"]
    for path in ("/v1/studies", "/v1/studies/{study_id}/analyses"):
        query_names = {item["name"] for item in current["paths"][path]["get"]["parameters"]}
        assert {"limit", "offset"} <= query_names
    study_queries = {
        item["name"] for item in current["paths"]["/v1/studies"]["get"]["parameters"]
    }
    assert {"track", "sort_by", "sort_order"} <= study_queries
    analysis_queries = {
        item["name"]
        for item in current["paths"]["/v1/studies/{study_id}/analyses"]["get"]["parameters"]
    }
    assert {"evidence_mode", "sort_order"} <= analysis_queries

