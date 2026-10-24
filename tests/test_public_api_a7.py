from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def test_public_route_aliases_request_ids_and_operator_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        health = client.get(
            "/v1/system/health",
            headers={"X-Request-ID": "audit-42", "X-AgentGuard-Token": "api-secret"},
        )
        assert health.status_code == 200
        assert health.headers["X-Request-ID"] == "audit-42"
        assert health.headers["X-Content-Type-Options"] == "nosniff"
        assert client.get(
            "/v1/system/capabilities", headers={"X-AgentGuard-Token": "api-secret"}
        ).status_code == 200

        denied = client.get("/v1/system/audit-events", headers={"X-AgentGuard-Token": "api-secret"})
        assert denied.status_code == 403
        events = client.get(
            "/v1/system/audit-events", headers={"X-AgentGuard-Operator-Token": "operator-secret"}
        )
        assert events.status_code == 200
        event = next(item for item in events.json()["items"] if item["request_id"] == "audit-42")
        assert event["path"] == "/v1/system/health"
        assert event["actor_role"] == "api"
        assert "secret" not in str(events.json())

        page = client.get("/v1/investigations?limit=1&offset=2", headers={"X-AgentGuard-Token": "api-secret"})
        assert page.status_code == 200
        assert page.json()["offset"] == 2
        assert page.json()["limit"] == 1
        assert page.json()["next_offset"] is None


def test_public_investigation_alias_preserves_unavailable_warden_state(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.post("/v1/investigations", json={
            "request_id": "no-kernel",
            "contract_id": "contract-a",
            "model_id": "model-a",
            "runtime_id": "runtime-a",
            "mode": "observation",
            "start_run": False,
        })
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "WARDEN_NOT_CONFIGURED"
        assert response.headers.get("X-Request-ID")


def test_openapi_keeps_legacy_paths_and_exposes_business_contracts(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/v1/system/health", "/v1/system/capabilities", "/v1/investigations",
        "/v1/investigations/{request_id}", "/v1/investigations/{request_id}/evidence",
        "/v1/evidence/investigations/{request_id}", "/v1/defense-policies/{request_id}",
        "/v1/reports/{run_id}", "/v1/kernel/workflows", "/v1/runs/{run_id}/report",
    ):
        assert path in paths
    request_schema_ref = paths["/v1/investigations"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"][request_schema_ref.rsplit("/", 1)[-1]]
    assert "request_id" in request_schema["required"]


def test_operator_token_is_distinct_when_configured(tmp_path, monkeypatch):
    from agentguard.contracts.experience import ExperienceRecommendationPolicy

    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "api-secret")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "operator-secret")
    policy = ExperienceRecommendationPolicy(policy_version="a7-policy", cost_penalty=0.4)
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        api_only = client.post(
            "/v1/experiences/recommendation-policies?activate=true",
            json=policy.model_dump(mode="json"),
            headers={"X-AgentGuard-Token": "api-secret"},
        )
        assert api_only.status_code == 403
        operator = client.post(
            "/v1/experiences/recommendation-policies?activate=true",
            json=policy.model_dump(mode="json"),
            headers={"X-AgentGuard-Operator-Token": "operator-secret"},
        )
        assert operator.status_code == 200


def test_rate_limit_and_cors_require_explicit_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_RATE_LIMIT_PER_MINUTE", "2")
    with TestClient(create_app(data_dir=tmp_path / "rate")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
        limited = client.get("/health")
        assert limited.status_code == 429
        assert limited.headers.get("Retry-After")

    monkeypatch.setenv("AGENTGUARD_CORS_ORIGINS", "*")
    try:
        create_app(data_dir=tmp_path / "wildcard")
    except ValueError as exc:
        assert "wildcard is forbidden" in str(exc)
    else:
        raise AssertionError("wildcard CORS must be rejected")

    monkeypatch.setenv("AGENTGUARD_RATE_LIMIT_PER_MINUTE", "300")
    monkeypatch.setenv("AGENTGUARD_CORS_ORIGINS", "https://console.example")
    with TestClient(create_app(data_dir=tmp_path / "cors")) as client:
        response = client.options(
            "/v1/system/capabilities",
            headers={
                "Origin": "https://console.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://console.example"
