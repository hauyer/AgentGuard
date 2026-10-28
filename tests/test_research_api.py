from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def registration_payload():
    return {
        "title": "Targeted defense preregistered study",
        "research_question": "Does targeted defense reduce risk without damaging benign utility?",
        "primary_hypothesis": "Treatment reduces risk exposure compared with matched control conditions.",
        "primary_outcome": "risk_exposure",
        "primary_direction": "decrease",
        "secondary_outcomes": ["benign_utility"],
        "analysis_method": "clustered_paired",
        "multiplicity_method": "holm",
        "alpha": 0.05,
        "planned_pairs": 20,
        "planned_case_families": 10,
        "seed": 13,
        "matching_variables": ["case_family", "seed", "model_config"],
        "inclusion_criteria": ["Authorized matched runs"],
        "exclusion_criteria": ["Runtime failure before metric collection"],
        "stopping_rule": "Stop after 20 matched pairs and report all failures.",
        "missing_data_rule": "Do not impute missing outcomes; report missing pairs.",
    }


def analysis_payload():
    families = [f"family-{i}" for i in range(10) for _ in range(2)]
    return {
        "outcomes": {
            "risk_exposure": {
                "control": [0.9, 0.88] * 10,
                "treatment": [0.3, 0.32] * 10,
                "case_families": families,
            },
            "benign_utility": {
                "control": [0.95, 0.94] * 10,
                "treatment": [0.93, 0.92] * 10,
                "case_families": families,
            },
        },
        "evidence_mode": "synthetic",
        "source_artifact_refs": [],
        "data_sha256": None,
        "bootstrap_draws": 200,
        "permutation_draws": 1000,
    }


def test_study_api_register_integrity_analysis_and_history(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
    headers = {"X-AgentGuard-Operator-Token": "study-operator"}
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        registered = client.post("/v1/studies", json=registration_payload(), headers=headers)
        assert registered.status_code == 201, registered.text
        study = registered.json()
        study_id = study["study_id"]
        integrity = client.get(f"/v1/studies/{study_id}/integrity", headers=headers)
        assert integrity.status_code == 200
        assert integrity.json()["integrity_valid"] is True

        analysis = client.post(f"/v1/studies/{study_id}/analyses", json=analysis_payload(), headers=headers)
        assert analysis.status_code == 201, analysis.text
        result = analysis.json()
        assert result["evidence_mode"] == "synthetic"
        assert result["report_artifact_id"]
        assert result["outcomes"]["risk_exposure"]["summary"]["n_case_families"] == 10
        assert result["confirmatory_primary_result"] is True
        assert result["causal_claim_status"] == "synthetic_only"
        assert result["source_validation"] == "synthetic_only"
        history = client.get(f"/v1/studies/{study_id}/analyses", headers=headers)
        assert history.status_code == 200
        assert history.json()["total"] == 1
        assert client.get("/v1/studies", headers=headers).json()["total"] == 1


def test_study_api_rejects_unregistered_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
    headers = {"X-AgentGuard-Operator-Token": "study-operator"}
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        study = client.post("/v1/studies", json=registration_payload(), headers=headers).json()
        payload = analysis_payload()
        payload["outcomes"]["unregistered_metric"] = payload["outcomes"]["risk_exposure"]
        response = client.post(f"/v1/studies/{study['study_id']}/analyses", json=payload, headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "STUDY_ANALYSIS_INVALID"


def test_study_writes_require_operator_or_approval_and_are_audited(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENTGUARD_OPERATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "study-approval")
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        denied = client.post("/v1/studies", json=registration_payload())
        assert denied.status_code == 403
        approved = client.post(
            "/v1/studies", json=registration_payload(),
            headers={"X-Approval-Token": "study-approval"},
        )
        assert approved.status_code == 201, approved.text
        study = approved.json()
        monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
        unauthorized_analysis = client.post(
            f"/v1/studies/{study['study_id']}/analyses", json=analysis_payload(),
        )
        assert unauthorized_analysis.status_code == 401
        monkeypatch.delenv("AGENTGUARD_OPERATOR_API_TOKEN", raising=False)
        route_denied_analysis = client.post(
            f"/v1/studies/{study['study_id']}/analyses", json=analysis_payload(),
        )
        assert route_denied_analysis.status_code == 403
        monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
        operator_analysis = client.post(
            f"/v1/studies/{study['study_id']}/analyses", json=analysis_payload(),
            headers={"X-AgentGuard-Operator-Token": "study-operator"},
        )
        assert operator_analysis.status_code == 201, operator_analysis.text
        events = client.get(
            "/v1/system/audit-events", headers={"X-AgentGuard-Operator-Token": "study-operator"},
        )
        assert events.status_code == 200
        writes = [
            item for item in events.json()["items"]
            if item["path"] in {"/v1/studies", "/v1/studies/{study_id}/analyses"}
        ]
        assert len(writes) == 4
        assert any(item["actor_role"] == "operator" and item["status_code"] == 201 for item in writes)


def test_research_capability_endpoint_is_read_only_and_reports_missing_warden(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    monkeypatch.delenv("AGENTGUARD_LIVE_WARDEN_URL", raising=False)
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        before = client.get("/v1/studies").json()["total"]
        response = client.get("/v1/research/capabilities")
        after = client.get("/v1/studies").json()["total"]
    assert response.status_code == 200
    report = response.json()
    assert report["backend_status"] == "not_configured"
    assert len(report["capabilities"]) == 11
    assert {item["status"] for item in report["capabilities"]} == {"UNAVAILABLE"}
    assert all("WARDEN_URL_NOT_CONFIGURED" in item["blockers"] for item in report["capabilities"])
    assert before == after == 0


def test_research_capability_endpoint_reports_misconfigured_warden_without_stack_details(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_WARDEN_URL", "http://remote.example")
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.get("/v1/research/capabilities")
    assert response.status_code == 200
    report = response.json()
    assert report["backend_status"] == "unavailable"
    assert all(item["status"] == "UNAVAILABLE" for item in report["capabilities"])
    assert all(item["blockers"] == ["WARDEN_CONFIGURATION_ERROR"] for item in report["capabilities"])
    assert "remote.example" not in response.text


def test_study_write_auth_layers_global_api_and_route_operator_or_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "global-api")
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "dedicated-operator")
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "study-approval")
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        assert client.post("/v1/studies", json=registration_payload()).status_code == 401
        api_only = client.post(
            "/v1/studies", json=registration_payload(),
            headers={"X-AgentGuard-Token": "global-api"},
        )
        assert api_only.status_code == 403
        api_and_approval = client.post(
            "/v1/studies", json=registration_payload(),
            headers={"X-AgentGuard-Token": "global-api", "X-Approval-Token": "study-approval"},
        )
        assert api_and_approval.status_code == 201, api_and_approval.text
        operator_as_global_auth = client.post(
            "/v1/studies", json=registration_payload(),
            headers={"X-AgentGuard-Operator-Token": "dedicated-operator"},
        )
        assert operator_as_global_auth.status_code == 201, operator_as_global_auth.text


def test_llm_agent_status_endpoint_does_not_make_network_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENTGUARD_LLM_INVESTIGATOR", raising=False)
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.get("/v1/agents/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["network_call_performed"] is False
        assert payload["llm_investigator_enabled"] is False
        assert payload["credentials_configured"] is False


def test_attribution_api_returns_artifact_link_and_does_not_claim_causal_validation(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        families = [f"family-{i}" for i in range(10) for _ in range(2)]
        node = client.post("/v1/attribution/nodes/analyze", json={
            "node_id": "receiver.layer.10",
            "node_type": "receiver_activation",
            "baseline_secret_recovery": [0.9, 0.88] * 10,
            "intervened_secret_recovery": [0.2, 0.25] * 10,
            "case_families": families,
            "evidence_mode": "synthetic",
            "bootstrap_draws": 200,
            "permutation_draws": 1000,
        })
        assert node.status_code == 201, node.text
        result = node.json()
        assert result["causal_claim_status"] == "candidate_only"
        assert result["report_artifact_id"]
        assert result["secret_recovery_mediation"]["mediation_contribution_baseline_minus_intervened"] > 0.5

        shift = client.post("/v1/attribution/path-shift", json={
            "protocol_fingerprint_before": "fixed-protocol-v1",
            "protocol_fingerprint_after": "fixed-protocol-v1",
            "model_state_before": "theta0",
            "model_state_after": "theta1",
            "contributions_before": {"sender.h8": 0.8, "token": 0.2},
            "contributions_after": {"sender.h8": 0.1, "token": 0.2, "receiver.h12": 0.7},
            "evidence_mode": "synthetic",
            "top_k": 2,
        })
        assert shift.status_code == 201, shift.text
        assert shift.json()["metrics"]["total_variation_distance"] > 0.5
        assert shift.json()["report_artifact_id"]
