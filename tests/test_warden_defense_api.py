from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def _completed_workflow(store):
    request = {
        "request_id": "workflow-defense-1",
        "contract_id": "contract-a",
        "model_id": "local-7b",
        "runtime_id": "stegopot-a",
        "scenario_id": "case-family-a",
        "risk_type": "secret_flow",
    }
    store.begin("workflow-defense-1", request)
    store.update("workflow-defense-1", "preflighting")
    store.update("workflow-defense-1", "plan_submitting", context={"stage": "plan_submitting"})
    plan = {"plan_id": "plan-defense-1", "status": "planned"}
    store.update("workflow-defense-1", "plan_created", context={"stage": "plan_created", "plan": plan})
    store.update("workflow-defense-1", "run_submitting", context={"stage": "run_submitting"})
    run = {
        "run_id": "run-defense-1",
        "plan_id": "plan-defense-1",
        "status": "completed",
        "evidence_bundle": {
            "items": [{
                "evidence_id": "evidence-defense-1",
                "type": "finding",
                "evidence_level": "observational",
                "risk_type": "secret_flow",
                "target_type": "message",
                "target_ref": "message-17",
                "artifact_refs": ["warden-artifact-17"],
            }],
        },
    }
    store.update(
        "workflow-defense-1",
        "run_submitted",
        context={"stage": "run_submitted", "run": run},
    )
    store.update(
        "workflow-defense-1",
        "finished",
        context={"plan": plan, "run": run},
        result={"status": "completed", "plan": plan, "run": run},
    )


def test_warden_evidence_proposes_candidates_and_approval_ends_unsupported(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "operator-token")
    app = create_app(data_dir=tmp_path / "data")
    _completed_workflow(app.state.warden_workflows)

    with TestClient(app) as client:
        preview_response = client.post("/v1/kernel/workflows/workflow-defense-1/defenses/preview")
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["run_id"] == "run-defense-1"
        assert preview["status"] == "preview_only"
        assert len(preview["candidates"]) == 2
        candidate = preview["candidates"][0]
        assert candidate["status"] == "proposed"
        assert candidate["target_ref"] == "message-17"
        assert candidate["evidence_refs"] == ["evidence-defense-1", "warden-artifact-17"]
        assert candidate["execution_supported"] is False
        assert candidate["expected_risk_reduction"] == 0.0
        assert any("unsupported" in note.lower() for note in preview["policy_notes"])

        body = {"candidate_id": candidate["candidate_id"], "authorization_ref": "approval-ticket-1"}
        waiting = client.post(
            "/v1/kernel/workflows/workflow-defense-1/defenses/execute",
            json=body,
        )
        assert waiting.status_code == 403
        assert waiting.json()["detail"]["defense_status"] == "awaiting_approval"

        unsupported = client.post(
            "/v1/kernel/workflows/workflow-defense-1/defenses/execute",
            json=body,
            headers={"X-Approval-Token": "operator-token"},
        )
        assert unsupported.status_code == 501
        assert unsupported.json()["detail"]["defense_status"] == "unsupported"
        assert "No defense action was submitted" in unsupported.json()["detail"]["message"]

    saved = app.state.warden_workflows.get_defense_candidate("workflow-defense-1", candidate["candidate_id"])
    assert saved["candidate"]["status"] == "unsupported"
    assert [event["to_status"] for event in saved["events"]] == [
        "proposed", "awaiting_approval", "approved", "unsupported",
    ]


def test_warden_defense_preview_blocks_incomplete_runs(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    request = {
        "request_id": "workflow-blocked",
        "contract_id": "contract-a",
        "model_id": "local-3b",
        "runtime_id": "stegopot-a",
    }
    app.state.warden_workflows.begin("workflow-blocked", request)

    with TestClient(app) as client:
        response = client.post("/v1/kernel/workflows/workflow-blocked/defenses/preview")
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["candidates"] == []
    assert app.state.warden_workflows.list_defense_candidates("workflow-blocked") == []
