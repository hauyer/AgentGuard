import time

from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def test_api_exposes_scenario_observations_findings_report_artifacts_and_preview(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    assert client.get("/console").status_code == 200
    assert client.get("/v1/scenarios/demo_cloud_security").status_code == 200
    response = client.post("/v1/runs", json={
        "scenario_id": "demo_cloud_security", "runtime_backend": "mock", "auto_intervene": False,
        "max_interventions": 2,
    })
    assert response.status_code == 200
    report = response.json(); run_id = report["run_id"]
    assert client.get(f"/v1/runs/{run_id}/observations").json()["items"]
    assert client.get(f"/v1/runs/{run_id}/findings").json()["items"]
    assert client.get(f"/v1/runs/{run_id}/report").status_code == 200
    preview = client.post(f"/v1/runs/{run_id}/defenses/preview", json={})
    assert preview.status_code == 200
    assert preview.json()["execution_performed"] is False
    assert client.get(f"/v1/runs/{run_id}/hypotheses").json()["items"]
    assert client.get(f"/v1/runs/{run_id}/investigation").status_code == 200
    assert client.get(f"/v1/runs/{run_id}/timeline").status_code == 200
    candidate = preview.json()["candidates"][0]
    executed = client.post(f"/v1/runs/{run_id}/defenses/execute", json={
        "candidate_id": candidate["candidate_id"], "authorization_ref": "test-ticket-1"
    })
    assert executed.status_code == 200, executed.text
    assert executed.json()["result"]["status"] == "completed"
    assert executed.json()["defense_status"] == "verification_pending"
    assert executed.json()["execution_mode"] == "synthetic"
    repeated = client.post(f"/v1/runs/{run_id}/defenses/execute", json={
        "candidate_id": candidate["candidate_id"], "authorization_ref": "test-ticket-1"
    })
    assert repeated.status_code == 200
    assert repeated.json().get("idempotent_replay") is True
    second_candidate = next(item for item in preview.json()["candidates"] if item["candidate_id"] != candidate["candidate_id"])
    second_execution = client.post(f"/v1/runs/{run_id}/defenses/execute", json={
        "candidate_id": second_candidate["candidate_id"], "authorization_ref": "test-ticket-2"
    })
    assert second_execution.status_code == 200, second_execution.text
    updated_report = client.get(f"/v1/runs/{run_id}/report").json()
    assert len(updated_report["intervention_history"]) == 2
    exhausted = client.post(f"/v1/runs/{run_id}/interventions", json={
        "experiment_id": updated_report["experiment_id"], "intervention_id": "third-action",
        "target_type": "message", "target_ref": "msg-002", "operation": "block",
        "parameters": {"scope": "synthetic_demo"}, "authorization_ref": "test-ticket-3"
    })
    assert exhausted.status_code == 403
    assert exhausted.json()["detail"]["error"] == "BUDGET_EXCEEDED"
    verification = client.post(f"/v1/runs/{run_id}/defenses/verify", json={
        "candidate_id": candidate["candidate_id"],
        "baseline_risk": [0.9, 0.8, 0.95, 0.85],
        "post_defense_risk": [0.2, 0.3, 0.2, 0.25],
        "baseline_utility": [0.95, 0.9, 0.96, 0.93],
        "post_defense_utility": [0.91, 0.89, 0.94, 0.91],
    })
    assert verification.status_code == 200, verification.text
    assert verification.json()["risk_reduction"] > 0
    assert verification.json()["status"] == "passed"
    assert verification.json()["verification_status"] == "unverified"
    assert verification.json()["defense_status"] == "verification_pending"
    assert client.get(f"/v1/runs/{run_id}/defenses/verification").status_code == 200
    evidence = client.get(f"/v1/runs/{run_id}/evidence").json()
    assert evidence["items"]
    assert evidence["items"][0]["source_system"] == "agentguard"
    assert evidence["items"][0]["evidence_level"] == "synthetic"
    assert evidence["items"][0]["verification_status"] == "unverified"
    artifact_id = evidence["report_artifact_id"]
    assert client.get(f"/v1/artifacts/{artifact_id}").status_code == 200
    assert client.get(f"/v1/artifacts/{artifact_id}/content").status_code == 200


def test_background_run_and_idempotency(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    response = client.post("/v1/runs?background=true", headers={"Idempotency-Key": "test-run-1"}, json={
        "scenario_id": "demo_cloud_security", "runtime_backend": "mock", "auto_intervene": False,
    })
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    for _ in range(100):
        state = client.get(f"/v1/runs/{job_id}").json()
        if state.get("status") in {"completed", "partial", "cancelled", "failed"}:
            break
        time.sleep(0.01)
    assert state.get("status") in {"completed", "partial"}
    repeat = client.post("/v1/runs?background=true", headers={"Idempotency-Key": "test-run-1"}, json={
        "scenario_id": "demo_cloud_security", "runtime_backend": "mock", "auto_intervene": False,
    })
    assert repeat.status_code == 200
    assert repeat.json().get("run_id") == job_id


def test_experience_api_accepts_unverified_memory_but_does_not_recommend_it(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    record = {
        "scenario_id": "release_review", "case_family": "family-a", "risk_type": "unauthorized_information_flow",
        "hypothesis_summary": "Message edge may violate policy", "action_type": "message_block",
        "action_summary": "Block one message edge", "outcome": "inconclusive", "evidence_grade": "synthetic",
    }
    added = client.post("/v1/experiences", json=record)
    assert added.status_code == 200
    searched = client.get("/v1/experiences?q=message+edge")
    assert searched.status_code == 200 and searched.json()["total"] == 1
    assert client.get("/v1/experiences/recommendations").json()["items"] == []


def test_probe_training_api_uses_case_family_isolated_split_and_stores_model_artifact(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    features = []
    labels = []
    groups = []
    for family in range(12):
        label = family % 2
        value = 2.0 if label else -2.0
        for offset in (0.0, 0.1):
            features.append([value + offset, value * 0.5 + offset])
            labels.append(label)
            groups.append(f"family-{family}")
    response = client.post("/v1/probes/train", json={
        "features": features, "labels": labels, "case_families": groups,
        "feature_names": ["activation_0", "activation_1"], "test_fraction": 0.25,
        "seed": 9, "epochs": 500,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["group_split_verified"] is True
    assert result["train_group_count"] + result["validation_group_count"] + result["test_group_count"] == 12
    assert result["validation_samples"] > 0
    assert 0 < result["threshold"] < 1
    assert result["metrics"]["auroc"] >= 0.9
    artifact = client.get(f"/v1/artifacts/{result['model_artifact_id']}/content")
    assert artifact.status_code == 200
    assert b"binary_logistic_linear_probe" in artifact.content
    prediction = client.post("/v1/probes/predict", json={
        "model_artifact_id": result["model_artifact_id"], "features": features[:2],
    })
    assert prediction.status_code == 200, prediction.text
    assert prediction.json()["predictions"] == [0, 0]


def test_api_token_middleware_protects_non_public_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "test-long-api-token")
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    assert client.get("/health").status_code == 200
    assert client.get("/v1/capabilities").status_code == 401
    assert client.get("/v1/capabilities", headers={"X-AgentGuard-Token": "test-long-api-token"}).status_code == 200


def test_background_run_supports_cooperative_cancellation(tmp_path):
    import time

    from agentguard.adapters.mock import MockBackend
    from agentguard.core.orchestrator import AgentGuardEngine

    class SlowMockBackend(MockBackend):
        def execute(self, scenario, request):
            time.sleep(0.35)
            return super().execute(scenario, request)

    backend = SlowMockBackend()
    engine = AgentGuardEngine(backend, backend, backend)
    with TestClient(create_app(engine, data_dir=tmp_path / "data")) as client:
        response = client.post("/v1/runs?background=true", json={
            "scenario_id": "demo_cloud_security", "runtime_backend": "mock", "auto_intervene": False,
        })
        job_id = response.json()["job_id"]
        for _ in range(100):
            state = client.get(f"/v1/runs/{job_id}").json()
            if state.get("status") == "running":
                break
            time.sleep(0.005)
        cancelled = client.post(f"/v1/runs/{job_id}/cancel")
        assert cancelled.status_code == 200
        for _ in range(200):
            state = client.get(f"/v1/runs/{job_id}").json()
            if state.get("status") in {"cancelled", "completed", "partial", "failed"}:
                break
            time.sleep(0.01)
        assert state.get("status") == "cancelled"
        assert state.get("report_status") == "partial"


def test_read_only_run_profile_blocks_defense_execution(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    response = client.post("/v1/runs", json={
        "scenario_id": "demo_cloud_security", "runtime_backend": "mock",
        "policy_profile": "read_only", "auto_intervene": False, "max_interventions": 2,
    })
    assert response.status_code == 200
    report = response.json()
    preview = client.post(f"/v1/runs/{report['run_id']}/defenses/preview", json={}).json()
    candidate = preview["candidates"][0]
    denied = client.post(f"/v1/runs/{report['run_id']}/defenses/execute", json={
        "candidate_id": candidate["candidate_id"], "authorization_ref": "read-only-test",
    })
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"] == "READ_ONLY_PROFILE"


def test_paired_experiment_api_executes_synthetic_matched_control_treatment_and_negative_control(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    response = client.post("/v1/runs", json={
        "scenario_id": "demo_cloud_security", "runtime_backend": "mock",
        "auto_intervene": False, "max_interventions": 5, "seed": 31,
    })
    assert response.status_code == 200
    report = response.json()
    design = report["experiment_designs"][0]
    execution = client.post(f"/v1/runs/{report['run_id']}/experiments/paired", json={
        "design_id": design["design_id"], "authorization_ref": "paired-test-approval",
    })
    assert execution.status_code == 200, execution.text
    result = execution.json()
    assert result["status"] == "completed"
    assert result["requested_pairs"] == result["completed_pairs"] == 5
    assert result["evidence_mode"] == "synthetic"
    assert result["causal_claim_status"] == "synthetic_only"
    assert result["aggregate_statistics"]["risk_exposure"]["mean_treatment_minus_control"] < 0
    assert result["negative_control_statistics"]["risk_exposure"]["mean_treatment_minus_control"] == 0
    assert client.get(f"/v1/runs/{report['run_id']}/experiments/paired").json()["items"]
    replay = client.post(f"/v1/runs/{report['run_id']}/experiments/paired", json={
        "design_id": design["design_id"], "authorization_ref": "paired-test-approval",
    })
    assert replay.status_code == 200
    assert replay.json()["artifact_id"] == result["artifact_id"]
    updated = client.get(f"/v1/runs/{report['run_id']}/report").json()
    assert updated["budget_usage"]["intervention_attempts"] == 5
    artifact = client.get(f"/v1/artifacts/{result['artifact_id']}/content")
    assert artifact.status_code == 200


def test_paired_experiment_api_checks_repetition_budget_before_running(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    response = client.post("/v1/runs", json={
        "scenario_id": "demo_cloud_security", "runtime_backend": "mock",
        "auto_intervene": False, "max_interventions": 2,
    })
    report = response.json(); design = report["experiment_designs"][0]
    result = client.post(f"/v1/runs/{report['run_id']}/experiments/paired", json={
        "design_id": design["design_id"], "authorization_ref": "paired-test-approval",
    })
    assert result.status_code == 403
    assert result.json()["detail"]["error"] == "BUDGET_EXCEEDED"
