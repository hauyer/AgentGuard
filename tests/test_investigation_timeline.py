"""Contract tests for the investigation timeline API (C4)."""

from fastapi.testclient import TestClient

from agentguard.api.app import create_app
from agentguard.services.investigation_timeline import InvestigationTimelineService


def _seeded_app(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    store = app.state.warden_workflows
    store.begin("inv-tl", {
        "request_id": "inv-tl", "contract_id": "contract-x", "model_id": "model-7b",
        "runtime_id": "runtime-x", "mode": "causal_audit", "scenario_id": "sc-1",
    })
    store.update("inv-tl", "preflighting", context={"stage": "preflighting"})
    store.update(
        "inv-tl", "plan_submitting", context={"stage": "plan_submitting"},
        detail={"operation": "create_audit_plan"},
    )
    store.update(
        "inv-tl", "plan_created",
        context={"stage": "plan_created", "plan": {"plan_id": "plan-tl"}},
        detail={"plan_id": "plan-tl", "plan_status": "planned"},
    )
    store.update(
        "inv-tl", "run_submitting", detail={"operation": "start_run", "plan_id": "plan-tl"},
    )
    store.update(
        "inv-tl", "submission_uncertain",
        error_code="WARDEN_TIMEOUT",
        detail={"exception_type": "WardenKernelError",
                "external_submission_may_have_succeeded": True},
    )
    store.update(
        "inv-tl", "run_submitted",
        context={"stage": "run_submitted", "run": {"run_id": "run-tl", "status": "running"}},
        detail={"reconciled": True, "run_id": "run-tl"},
    )
    store.update(
        "inv-tl", "polling",
        detail={"remote_status_from": "running", "remote_status_to": "completed"},
    )
    store.update(
        "inv-tl", "finished",
        context={"stage": "finished"}, result={"status": "completed"},
        detail={"result_status": "completed"},
    )
    return app


def test_full_lifecycle_projects_real_journal_events(tmp_path):
    app = _seeded_app(tmp_path)
    service: InvestigationTimelineService = app.state.investigation_timeline
    response = service.timeline("inv-tl")
    assert response is not None and response.available is True
    types = [event.event_type for event in response.items]
    assert types == [
        "workflow_created", "validation", "plan_submission", "plan_submission",
        "run_submission", "run_submission", "reconciliation", "run_status_change",
        "state_transition",
    ]
    first = response.items[0]
    assert first.phase == "workflow_created"
    assert first.status == "completed"
    assert first.source == "warden_workflow_journal"
    uncertain = next(e for e in response.items if e.reason_code == "WARDEN_TIMEOUT")
    assert uncertain.status == "ambiguous"
    assert uncertain.event_type == "run_submission"
    plan_events = [e for e in response.items if e.related_plan_id == "plan-tl"]
    assert plan_events
    run_events = [e for e in response.items if e.related_run_id == "run-tl"]
    assert run_events
    last = response.items[-1]
    assert last.phase == "completion"
    assert response.next_event_id == last.event_id


def test_fresh_workflow_has_only_the_created_event_and_no_synthesis(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    app.state.warden_workflows.begin("inv-empty", {"request_id": "inv-empty"})
    service: InvestigationTimelineService = app.state.investigation_timeline
    response = service.timeline("inv-empty")
    assert response is not None
    assert response.available is True
    # Only the journal's own creation row is projected; nothing beyond it exists.
    assert [event.event_type for event in response.items] == ["workflow_created"]
    assert response.next_event_id == response.items[0].event_id
    assert response.items[0].related_plan_id is None
    assert response.items[0].related_run_id is None


def test_unknown_workflow_returns_none_for_404(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    assert app.state.investigation_timeline.timeline("inv-none") is None


def test_incremental_fetch_after_event_id(tmp_path):
    app = _seeded_app(tmp_path)
    service: InvestigationTimelineService = app.state.investigation_timeline
    full = service.timeline("inv-tl")
    assert full is not None
    pivot = full.items[3].event_id
    incremental = service.timeline("inv-tl", after_event_id=pivot)
    assert incremental is not None
    assert [event.event_id for event in incremental.items] == [
        event.event_id for event in full.items if event.event_id > pivot
    ]
    caught_up = service.timeline("inv-tl", after_event_id=full.next_event_id)
    assert caught_up is not None
    assert caught_up.items == []
    assert any("No new events" in note for note in caught_up.limitations)


def test_cancelled_workflow_projects_cancellation_event(tmp_path):
    from agentguard.services.warden_lifecycle import WardenLifecycleService

    app = create_app(data_dir=tmp_path / "data")
    store = app.state.warden_workflows
    store.begin("inv-cancel", {"request_id": "inv-cancel"})
    WardenLifecycleService.cancel_pending(store, "inv-cancel")
    service: InvestigationTimelineService = app.state.investigation_timeline
    response = service.timeline("inv-cancel")
    assert response is not None
    assert response.items[-1].event_type == "cancellation"
    assert response.items[-1].phase == "cancellation"
    assert response.items[-1].status == "completed"
    assert "cancel_local_pending" in response.items[-1].message


def test_timeline_survives_restart_and_honors_journal_order(tmp_path):
    data_dir = tmp_path / "data"
    _seeded_app(tmp_path)
    reopened = create_app(data_dir=data_dir)
    service: InvestigationTimelineService = reopened.state.investigation_timeline
    response = service.timeline("inv-tl")
    assert response is not None
    ids = [event.event_id for event in response.items]
    assert ids == sorted(ids)
    assert len(ids) == 9


def test_unexpected_detail_keys_do_not_leak_into_messages(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    store = app.state.warden_workflows
    store.begin("inv-leak", {"request_id": "inv-leak"})
    store.update(
        "inv-leak", "preflighting",
        detail={"operation": "process_workflow", "secret_payload": "s3cret", "truth": 1},
    )
    service: InvestigationTimelineService = app.state.investigation_timeline
    response = service.timeline("inv-leak")
    assert response is not None
    dumped = response.model_dump_json()
    assert "s3cret" not in dumped
    assert "truth" not in dumped


def test_api_timeline_route_and_404(tmp_path):
    app = _seeded_app(tmp_path)
    with TestClient(app) as client:
        ok = client.get("/v1/investigations/inv-tl/timeline")
        assert ok.status_code == 200
        body = ok.json()
        assert body["schema_version"] == "1.0"
        assert body["workflow_status"] == "completed"
        assert body["count"] == 9
        assert ok.headers.get("X-Request-ID")
        missing = client.get("/v1/investigations/inv-none/timeline")
        assert missing.status_code == 404
        assert missing.json()["detail"]["error"] == "WORKFLOW_NOT_FOUND"
        invalid = client.get("/v1/investigations/inv-tl/timeline?after_event_id=0")
        assert invalid.status_code == 422
        schema = client.get("/openapi.json").json()
    assert "get" in schema["paths"]["/v1/investigations/{request_id}/timeline"]
