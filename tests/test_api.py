from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def test_api_health_and_run():
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    capabilities = client.get("/v1/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["provider_id"] == "mock"
    response = client.post(
        "/v1/runs",
        json={
            "scenario_id": "demo_cloud_security",
            "goal": "test",
            "max_interventions": 1,
            "max_runtime_seconds": 120,
            "runtime_backend": "mock",
            "seed": 7,
            "tags": [],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["backend_mode"] == "synthetic"
    assert data["causal_claim_status"] == "simulated_only"
    run_id = data["run_id"]
    assert client.get(f"/v1/runs/{run_id}").status_code == 200
    evidence = client.get(f"/v1/runs/{run_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["run_id"] == run_id
    assert evidence.json()["items"]


def test_api_unknown_scenario_returns_404():
    client = TestClient(create_app())
    response = client.post("/v1/runs", json={"scenario_id": "nope"})
    assert response.status_code == 404


def test_experience_bandit_endpoint_uses_verified_records_only(tmp_path):
    from agentguard.contracts.experience import ExperienceRecord

    client = TestClient(create_app(data_dir=tmp_path / "bandit-api"))
    for index, outcome in enumerate(["success", "success"]):
        record = {
            "experience_id": f"bandit-good-{index}",
            "scenario_id": "demo_cloud_security",
            "case_family": f"family-{index}",
            "dataset_partition": "training",
            "risk_type": "unauthorized_information_flow",
            "hypothesis_summary": "test",
            "action_type": "block_tool_result",
            "action_summary": "test",
            "outcome": outcome,
            "evidence_grade": "interventional",
            "verification_status": "verified",
            "repetitions": 2,
            "evidence_refs": [f"artifact:{index}"],
            "validation_notes": ["reviewed paired outcome"],
            "allow_auto_recommendation": True,
            "source_system": "warden",
            "source_run_status": "completed",
            "evidence_provenance_verified": True,
            "audit_run_id": f"audit-bandit-good-{index}",
            "metrics": {"risk_reduction": 0.3},
            "observed_outcome": {"risk_reduction": 0.3},
            "benign_utility": {"task_success_rate": 0.99},
            "execution_cost": 0.1,
            "model_version": "model-3b-v1",
            "policy_version": "policy-v1",
            "reproducibility_hash": f"{'a' if index == 0 else 'b'}" * 64,
            "verified_at": "2026-01-01T00:00:00+00:00",
        }
        client.app.state.experiences.add(ExperienceRecord.model_validate(record))
    response = client.get(
        "/v1/experiences/bandit-recommendations",
        params={"scenario_id": "demo_cloud_security", "seed": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eligible_record_count"] == 2
    assert data["items"][0]["action_type"] == "block_tool_result"
    assert data["execution_authorized"] is False
    invalid_partition = client.get(
        "/v1/experiences/bandit-recommendations", params={"target_partition": "public"}
    )
    assert invalid_partition.status_code == 422


def test_experience_policy_changes_require_api_auth_and_support_rollback(tmp_path, monkeypatch):
    from agentguard.contracts.experience import ExperienceRecommendationPolicy

    monkeypatch.delenv("AGENTGUARD_API_TOKEN", raising=False)
    client = TestClient(create_app(data_dir=tmp_path / "policy-api"))
    policy = ExperienceRecommendationPolicy(
        policy_version="experience-thompson-test-v2", cost_penalty=0.5, cost_scale=2.0
    )
    denied = client.post(
        "/v1/experiences/recommendation-policies?activate=true",
        json=policy.model_dump(mode="json"),
    )
    assert denied.status_code == 403

    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "test-only-token")
    headers = {"X-AgentGuard-Token": "test-only-token"}
    created = client.post(
        "/v1/experiences/recommendation-policies?activate=true",
        json=policy.model_dump(mode="json"),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert client.get(
        "/v1/experiences/recommendation-policies", headers=headers
    ).json()["active"]["policy_version"] == policy.policy_version

    rolled_back = client.post(
        "/v1/experiences/recommendation-policies/experience-thompson-v1/rollback?reason=validation_regression",
        headers=headers,
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["active"]["policy_version"] == "experience-thompson-v1"
