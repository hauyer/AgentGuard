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


def test_experience_bandit_endpoint_uses_verified_records_only():
    from agentguard.contracts.experience import ExperienceRecord

    client = TestClient(create_app())
    for index, outcome in enumerate(["success", "success"]):
        client.post("/v1/experiences", json={
            "experience_id": f"bandit-good-{index}",
            "scenario_id": "demo_cloud_security",
            "case_family": f"family-{index}",
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
        })
    response = client.get(
        "/v1/experiences/bandit-recommendations",
        params={"scenario_id": "demo_cloud_security", "seed": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eligible_record_count"] == 2
    assert data["items"][0]["action_type"] == "block_tool_result"
    assert data["execution_authorized"] is False
