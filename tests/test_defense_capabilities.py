"""Contract tests for the defense capability description fields (C6)."""

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.contracts.defense import DefenseCandidate, DefenseLifecycleRecord


def _completed_workflow(store, request_id="workflow-cap-1"):
    request = {
        "request_id": request_id,
        "contract_id": "contract-a",
        "model_id": "local-7b",
        "runtime_id": "stegopot-a",
        "scenario_id": "case-family-a",
        "risk_type": "secret_flow",
    }
    store.begin(request_id, request)
    run = {
        "run_id": f"run-{request_id}",
        "plan_id": f"plan-{request_id}",
        "status": "completed",
        "evidence_bundle": {"items": [{
            "evidence_id": f"evidence-{request_id}",
            "type": "finding",
            "evidence_level": "observational",
            "risk_type": "secret_flow",
            "target_type": "message",
            "target_ref": "message-17",
            "artifact_refs": ["warden-artifact-17"],
        }]},
    }
    store.update(request_id, "finished", context={
        "plan": {"plan_id": run["plan_id"]}, "run": run,
    },
                 result={"status": "completed", "run": run})


def test_preview_carries_independent_capability_flags(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    _completed_workflow(app.state.warden_workflows)
    with TestClient(app) as client:
        preview = client.post("/v1/kernel/workflows/workflow-cap-1/defenses/preview").json()
    assert preview["approval_required_per_candidate"] is True
    assert preview["execution_supported_overall"] is False
    assert preview["rollback_supported"] is False
    assert preview["verification_evidence_available"] is False
    assert preview["benign_utility_data_available"] is False
    notes = " ".join(preview["capability_notes"])
    assert "DEP-007" in notes
    assert "DEP-003" in notes
    assert all(candidate["execution_supported"] is False for candidate in preview["candidates"])


def test_approval_never_flips_execution_support(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "operator-token")
    app = create_app(data_dir=tmp_path / "data")
    _completed_workflow(app.state.warden_workflows)
    with TestClient(app) as client:
        preview = client.post("/v1/kernel/workflows/workflow-cap-1/defenses/preview").json()
        candidate_id = preview["candidates"][0]["candidate_id"]
        approved = client.post(
            "/v1/defense-policies/workflow-cap-1/execute",
            json={"candidate_id": candidate_id, "authorization_ref": "ticket-1"},
            headers={"X-Approval-Token": "operator-token"},
        )
        assert approved.status_code == 501
        detail = approved.json()["detail"]
        assert detail["error"] == "WARDEN_DEFENSE_EXECUTOR_UNSUPPORTED"
        assert detail["defense_status"] == "unsupported"
        # After recorded approval, the preview still reports no execution support.
        after = client.get("/v1/defense-policies/workflow-cap-1").json()
        assert after["execution_supported_overall"] is False
        assert after["execution_performed"] is False
        saved = next(
            item for item in after["candidates"] if item["candidate_id"] == candidate_id
        )
        assert saved["status"] == "unsupported"


def test_verified_candidate_flips_verification_evidence_flag(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    _completed_workflow(app.state.warden_workflows, "workflow-cap-2")
    store = app.state.warden_workflows
    with TestClient(app) as client:
        preview = client.post("/v1/kernel/workflows/workflow-cap-2/defenses/preview").json()
        assert preview["verification_evidence_available"] is False
    # A separately recorded verified candidate (new id; the store keeps
    # existing lifetimes on preview regeneration).
    candidate = DefenseCandidate(
        candidate_id="def-verified-c5",
        finding_id="finding-verified",
        target_type="message",
        target_ref="message-17",
        operation="block",
        expected_risk_reduction=0.5,
        estimated_benign_utility_cost=0.1,
        rationale="verified historical candidate",
    )
    lifecycle = DefenseLifecycleRecord(
        candidate_id=candidate.candidate_id, status="verified",
        execution_scope="isolated_test_environment",
        history=[],
    )
    store.save_defense_candidates("workflow-cap-2", [
        (candidate.model_dump(mode="json"), lifecycle.model_dump(mode="json")),
    ])
    with TestClient(app) as client:
        after = client.get("/v1/defense-policies/workflow-cap-2").json()
    assert after["verification_evidence_available"] is True
    # Verification evidence still does not imply execution or rollback support.
    assert after["execution_supported_overall"] is False
    assert after["rollback_supported"] is False


def test_engine_run_preview_keeps_reference_only_verification_semantics(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    with TestClient(app) as client:
        report = client.post("/v1/runs", json={"scenario_id": scenario}).json()
        preview = client.get(f"/v1/runs/{report['run_id']}/defenses/preview").json()
    assert preview["execution_supported_overall"] is False
    assert preview["verification_evidence_available"] is False
    assert preview["benign_utility_data_available"] is False
    assert preview["execution_performed"] is False
