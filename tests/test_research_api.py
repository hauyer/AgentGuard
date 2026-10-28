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


def test_study_api_idempotency_pagination_and_measured_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
    headers = {"X-AgentGuard-Operator-Token": "study-operator"}
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        payload = registration_payload()
        first = client.post(
            "/v1/studies", json=payload,
            headers={**headers, "Idempotency-Key": "study-reg-1"},
        )
        replay = client.post(
            "/v1/studies", json=payload,
            headers={**headers, "Idempotency-Key": "study-reg-1"},
        )
        assert first.status_code == replay.status_code == 201
        assert first.json()["study_id"] == replay.json()["study_id"]
        conflict = client.post(
            "/v1/studies", json={**payload, "seed": 99},
            headers={**headers, "Idempotency-Key": "study-reg-1"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error"] == "IDEMPOTENCY_CONFLICT"

        for seed in (20, 21):
            client.post("/v1/studies", json={**payload, "seed": seed}, headers=headers)
        page = client.get(
            "/v1/studies?limit=2&offset=0&sort_by=title&sort_order=asc", headers=headers,
        ).json()
        assert page["total"] == 3 and len(page["items"]) == 2
        assert page["next_offset"] == 2
        assert page["sort_by"] == "title" and page["sort_order"] == "asc"
        tail = client.get("/v1/studies?limit=2&offset=2", headers=headers).json()
        assert len(tail["items"]) == 1 and tail["next_offset"] is None
        filtered = client.get(
            "/v1/studies?track=live_steganography", headers=headers,
        ).json()
        assert filtered["total"] == 0 and filtered["track"] == "live_steganography"

        study_id = first.json()["study_id"]
        analysis_headers = {**headers, "Idempotency-Key": "analysis-key-1"}
        analysis = client.post(
            f"/v1/studies/{study_id}/analyses", json=analysis_payload(), headers=analysis_headers,
        )
        analysis_replay = client.post(
            f"/v1/studies/{study_id}/analyses", json=analysis_payload(), headers=analysis_headers,
        )
        assert analysis.status_code == analysis_replay.status_code == 201
        assert analysis.json()["analysis_id"] == analysis_replay.json()["analysis_id"]
        analysis_conflict_payload = analysis_payload()
        analysis_conflict_payload["permutation_draws"] = 2000
        analysis_conflict = client.post(
            f"/v1/studies/{study_id}/analyses", json=analysis_conflict_payload,
            headers=analysis_headers,
        )
        assert analysis_conflict.status_code == 409
        assert analysis_conflict.json()["detail"]["error"] == "IDEMPOTENCY_CONFLICT"
        analysis_page = client.get(
            f"/v1/studies/{study_id}/analyses?limit=1&offset=0&evidence_mode=synthetic", headers=headers,
        ).json()
        assert analysis_page["total"] == 1 and analysis_page["next_offset"] is None
        assert analysis_page["evidence_mode"] == "synthetic"

        measured = analysis_payload()
        measured["evidence_mode"] = "measured"
        blocked = client.post(
            f"/v1/studies/{study_id}/analyses", json=measured, headers=headers,
        )
        assert blocked.status_code == 422
        assert blocked.json()["detail"]["error"] == "ARTIFACT_CONTENT_NOT_VERIFIED"


def test_study_analysis_script_replay_stays_separate_from_synthetic_and_observed(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
    headers = {"X-AgentGuard-Operator-Token": "study-operator"}
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        study = client.post("/v1/studies", json=registration_payload(), headers=headers).json()
        replay = analysis_payload()
        replay["evidence_mode"] = "script_replay"
        response = client.post(
            f"/v1/studies/{study['study_id']}/analyses", json=replay, headers=headers,
        )
    assert response.status_code == 201, response.text
    assert response.json()["source_validation"] == "script_replay"
    assert response.json()["causal_claim_status"] == "not_assessed"


def test_study_and_analysis_idempotency_recover_after_api_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
    headers = {
        "X-AgentGuard-Operator-Token": "study-operator",
        "Idempotency-Key": "restart-study-key",
    }
    data_dir = tmp_path / "persistent-data"
    with TestClient(create_app(data_dir=data_dir)) as client:
        study = client.post("/v1/studies", json=registration_payload(), headers=headers).json()
        analysis_headers = {**headers, "Idempotency-Key": "restart-analysis-key"}
        analysis = client.post(
            f"/v1/studies/{study['study_id']}/analyses", json=analysis_payload(),
            headers=analysis_headers,
        ).json()

    with TestClient(create_app(data_dir=data_dir)) as restarted:
        study_replay = restarted.post(
            "/v1/studies", json=registration_payload(), headers=headers,
        )
        analysis_replay = restarted.post(
            f"/v1/studies/{study['study_id']}/analyses", json=analysis_payload(),
            headers=analysis_headers,
        )
        history = restarted.get(
            f"/v1/studies/{study['study_id']}/analyses", headers=headers,
        ).json()
    assert study_replay.status_code == analysis_replay.status_code == 201
    assert study_replay.json()["study_id"] == study["study_id"]
    assert analysis_replay.json()["analysis_id"] == analysis["analysis_id"]
    assert history["total"] == 1


def test_research_warden_workflow_requires_operator_or_approval_before_remote_lookup(tmp_path, monkeypatch):
    for name in (
        "AGENTGUARD_API_TOKEN", "AGENTGUARD_OPERATOR_API_TOKEN", "AGENTGUARD_APPROVAL_TOKEN",
        "AGENTGUARD_WARDEN_URL", "AGENTGUARD_WARDEN_RESEARCH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    payload = {
        "request_id": "study-run-1", "contract_id": "contract-1", "model_id": "model-1",
        "runtime_id": "runtime-1", "research_protocol_ref": "protocol:study-1",
        "study_arm_ref": "study-1/treatment", "study_id": "study-1", "study_arm_id": "treatment",
    }
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        denied = client.post("/v1/workflows", json=payload)
        assert denied.status_code == 403
        assert denied.json()["detail"]["error"] == "OPERATOR_TOKEN_REQUIRED"
        monkeypatch.setenv("AGENTGUARD_OPERATOR_API_TOKEN", "study-operator")
        authorized_but_unconfigured = client.post(
            "/v1/workflows", json=payload,
            headers={"X-AgentGuard-Operator-Token": "study-operator"},
        )
    assert authorized_but_unconfigured.status_code == 503
    assert authorized_but_unconfigured.json()["detail"]["error"] == "WARDEN_NOT_CONFIGURED"


def test_research_write_rejects_oversized_declared_body_before_parsing(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.post(
            "/v1/studies", content=b"", headers={"Content-Length": str(16 * 1024 * 1024 + 1)},
        )
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "REQUEST_BODY_TOO_LARGE"


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
        successful_writes = [item for item in writes if item["status_code"] == 201]
        assert all(len(item["payload_sha256"]) == 64 for item in successful_writes)
        assert {item["approval_status"] for item in successful_writes} == {
            "approval_authorized", "operator_authorized",
        }


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
