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
        assert event["completed"] is True
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
    expected_routes = {
        "/console": {"get"},
        "/health": {"get"},
        "/v1/agents/status": {"get"},
        "/v1/capabilities": {"get"},
        "/v1/system/health": {"get"},
        "/v1/system/capabilities": {"get"},
        "/v1/system/audit-events": {"get"},
        "/v1/kernel/status": {"get"},
        "/v1/kernel/doctor": {"get"},
        "/v1/kernel/capabilities": {"get"},
        "/v1/kernel/resources": {"get"},
        "/v1/kernel/contracts": {"get"},
        "/v1/kernel/audits": {"post"},
        "/v1/kernel/audits/{plan_id}": {"get"},
        "/v1/kernel/runs/{run_id}": {"get"},
        "/v1/kernel/workflows": {"get", "post"},
        "/v1/kernel/workflows/{request_id}": {"get"},
        "/v1/kernel/workflows/{request_id}/cancel": {"post"},
        "/v1/kernel/workflows/{request_id}/evidence": {"get"},
        "/v1/kernel/workflows/{request_id}/events": {"get"},
        "/v1/kernel/workflows/{request_id}/defenses/preview": {"get", "post"},
        "/v1/kernel/workflows/{request_id}/defenses/execute": {"post"},
        "/v1/investigations": {"get", "post"},
        "/v1/investigations/{request_id}": {"get"},
        "/v1/investigations/{request_id}/evidence": {"get"},
        "/v1/evidence/investigations/{request_id}": {"get"},
        "/v1/workflows": {"get", "post"},
        "/v1/workflows/{request_id}": {"get"},
        "/v1/defense-policies/{request_id}": {"get"},
        "/v1/defense-policies/{request_id}/preview": {"post"},
        "/v1/defense-policies/{request_id}/execute": {"post"},
        "/v1/reports/{run_id}": {"get"},
        "/v1/scenarios": {"get"},
        "/v1/scenarios/{scenario_id}": {"get"},
        "/v1/artifacts": {"get"},
        "/v1/artifacts/{artifact_id}": {"get"},
        "/v1/artifacts/{artifact_id}/content": {"get"},
        "/v1/runs": {"get", "post"},
        "/v1/runs/{run_id}": {"get"},
        "/v1/runs/{run_id}/cancel": {"post"},
        "/v1/runs/{run_id}/observations": {"get"},
        "/v1/runs/{run_id}/graph": {"get"},
        "/v1/runs/{run_id}/hypotheses": {"get"},
        "/v1/runs/{run_id}/investigation": {"get"},
        "/v1/runs/{run_id}/timeline": {"get"},
        "/v1/runs/{run_id}/findings": {"get"},
        "/v1/runs/{run_id}/evidence": {"get"},
        "/v1/runs/{run_id}/report": {"get"},
        "/v1/runs/{run_id}/interventions": {"post"},
        "/v1/runs/{run_id}/defenses/preview": {"get", "post"},
        "/v1/runs/{run_id}/defenses/execute": {"post"},
        "/v1/runs/{run_id}/defenses/verify": {"post"},
        "/v1/runs/{run_id}/defenses/verification": {"get"},
        "/v1/runs/{run_id}/experiments/paired": {"get", "post"},
        "/v1/probes/train": {"post"},
        "/v1/probes/predict": {"post"},
        "/v1/experiences": {"get", "post"},
        "/v1/experiences/recommendations": {"get"},
        "/v1/experiences/recommendation-policies": {"get", "post"},
        "/v1/experiences/recommendation-policies/{policy_version}/rollback": {"post"},
        "/v1/experiences/bandit-recommendations": {"get"},
        "/v1/experiences/action-statistics": {"get"},
        "/v1/attribution/nodes/analyze": {"post"},
        "/v1/attribution/path-shift": {"post"},
        "/v1/studies": {"get", "post"},
        "/v1/studies/{study_id}": {"get"},
        "/v1/studies/{study_id}/integrity": {"get"},
        "/v1/studies/{study_id}/analyses": {"get", "post"},
    }
    for path, methods in expected_routes.items():
        assert path in paths
        assert methods <= set(paths[path])

    request_schema_ref = paths["/v1/investigations"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"][request_schema_ref.rsplit("/", 1)[-1]]
    assert "request_id" in request_schema["required"]
    assert paths["/v1/kernel/workflows"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"] == request_schema_ref
    assert paths["/v1/probes/train"]["post"]["deprecated"] is True
    assert paths["/v1/probes/predict"]["post"]["deprecated"] is True
    list_parameters = paths["/v1/investigations"]["get"]["parameters"]
    assert {parameter["name"] for parameter in list_parameters} >= {"limit", "offset", "status"}


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
        loop_preflight = client.options(
            "/v1/investigation-loops",
            headers={
                "Origin": "https://console.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "x-agentguard-operator-token,x-loop-idempotency-key"
                ),
            },
        )
        assert loop_preflight.status_code == 200
        allowed_headers = loop_preflight.headers["access-control-allow-headers"].lower()
        assert "x-agentguard-operator-token" in allowed_headers
        assert "x-loop-idempotency-key" in allowed_headers


def test_api_fails_closed_if_audit_reservation_is_unavailable(tmp_path, monkeypatch, caplog):
    app = create_app(data_dir=tmp_path / "audit-failure")
    hidden_value = "audit-error-oracle-sentinel"

    def unavailable(**kwargs):
        raise OSError(hidden_value)

    monkeypatch.setattr(app.state.warden_workflows, "begin_api_audit_event", unavailable)
    with TestClient(app) as client:
        response = client.get("/v1/system/health")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "API_AUDIT_UNAVAILABLE"
    assert response.headers.get("X-Request-ID")
    assert hidden_value not in caplog.text


def test_unhandled_api_exception_log_does_not_echo_exception_message(tmp_path, caplog):
    app = create_app(data_dir=tmp_path / "exception-log")
    hidden_value = "request-error-oracle-sentinel"

    def fail_with_sensitive_exception() -> dict[str, str]:
        raise RuntimeError(hidden_value)

    app.add_api_route("/test-sensitive-error", fail_with_sensitive_exception, methods=["GET"])
    with TestClient(app) as client:
        response = client.get("/test-sensitive-error")

    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "INTERNAL_SERVER_ERROR"
    assert hidden_value not in response.text
    assert hidden_value not in caplog.text


def test_hidden_truth_metadata_is_rejected_without_api_or_sqlite_leak(tmp_path, caplog):
    app = create_app(data_dir=tmp_path / "truth-boundary")
    hidden_value = "oracle-sentinel-must-not-escape"
    with TestClient(app) as client:
        response = client.post("/v1/investigations", json={
            "request_id": "truth-boundary",
            "contract_id": "contract-1",
            "model_id": "model-1",
            "runtime_id": "runtime-1",
            "mode": "observation",
            "metadata": {"nested": [{"oracleLabel": hidden_value}]},
            "start_run": False,
        })

    assert response.status_code == 422
    assert hidden_value not in response.text
    assert "Invalid request" in response.text
    assert hidden_value not in caplog.text
    assert hidden_value.encode("utf-8") not in app.state.warden_workflows.path.read_bytes()
    assert app.state.warden_workflows.list(limit=10) == []
