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


def test_study_api_register_integrity_analysis_and_history(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        registered = client.post("/v1/studies", json=registration_payload())
        assert registered.status_code == 201, registered.text
        study = registered.json()
        study_id = study["study_id"]
        integrity = client.get(f"/v1/studies/{study_id}/integrity")
        assert integrity.status_code == 200
        assert integrity.json()["integrity_valid"] is True

        analysis = client.post(f"/v1/studies/{study_id}/analyses", json=analysis_payload())
        assert analysis.status_code == 201, analysis.text
        result = analysis.json()
        assert result["evidence_mode"] == "synthetic"
        assert result["report_artifact_id"]
        assert result["outcomes"]["risk_exposure"]["summary"]["n_case_families"] == 10
        assert result["confirmatory_primary_result"] is True
        history = client.get(f"/v1/studies/{study_id}/analyses")
        assert history.status_code == 200
        assert history.json()["total"] == 1
        assert client.get("/v1/studies").json()["total"] == 1


def test_study_api_rejects_unregistered_endpoint(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        study = client.post("/v1/studies", json=registration_payload()).json()
        payload = analysis_payload()
        payload["outcomes"]["unregistered_metric"] = payload["outcomes"]["risk_exposure"]
        response = client.post(f"/v1/studies/{study['study_id']}/analyses", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "STUDY_ANALYSIS_INVALID"


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
