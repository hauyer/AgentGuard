"""Opt-in live validation of the C1-C4 endpoints against a real WARDEN process.

Requires `AGENTGUARD_LIVE_WARDEN_URL` to point at a running kernel (plan-level
composition is enough: the dataset provides contracts; a model runtime is not
required — its absence is exactly what readiness must report honestly).
"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient
from agentguard.api.app import create_app


def _live_client() -> WardenKernelClient:
    url = os.environ.get("AGENTGUARD_LIVE_WARDEN_URL") or os.environ.get("AGENTGUARD_WARDEN_URL")
    if not url:
        pytest.skip("AGENTGUARD_LIVE_WARDEN_URL is not configured")
    try:
        client = WardenKernelClient(url)
        assert client.health()["api_version"] == "v1"
        return client
    except Exception as exc:  # noqa: BLE001 - any transport failure means not live
        pytest.skip(f"live WARDEN unreachable: {type(exc).__name__}")


def _live_app(tmp_path, monkeypatch, kernel):
    monkeypatch.setenv("AGENTGUARD_WARDEN_URL", kernel.base_url)
    app = create_app(data_dir=tmp_path / "data")
    app.state.system_readiness = type(app.state.system_readiness)(
        lambda: (kernel, None), ttl_seconds=0.001,
    )
    return app


def _runtime_available(kernel) -> bool:
    report = next(
        (item for item in kernel.capabilities() if item.get("component_id") == "agent.runtime"),
        None,
    )
    return bool(report and report.get("available") is True)


def _declared_runtime_ids(kernel) -> list[str]:
    report = next(
        (item for item in kernel.capabilities() if item.get("component_id") == "agent.runtime"),
        None,
    )
    metadata = (report or {}).get("metadata") or {}
    ids = []
    if isinstance(metadata.get("runtime_id"), str):
        ids.append(metadata["runtime_id"])
    for model_id in metadata.get("model_ids") or []:
        if isinstance(model_id, str):
            ids.append(model_id)
    return ids


def test_live_warden_full_observation_run_projects_real_message_edges(tmp_path, monkeypatch):
    """End-to-end real chain: AgentGuard -> WARDEN -> local model -> C1 graph.

    Requires a live kernel with a working agent runtime (e.g. the StegoPot
    observation composition). Skips honestly otherwise.
    """
    kernel = _live_client()
    contracts = kernel.contracts()
    if not contracts:
        pytest.skip("The live WARDEN service has no threat contracts")
    if not _runtime_available(kernel):
        pytest.skip("live WARDEN has no available agent.runtime; full observation not executable")
    runtime_ids = _declared_runtime_ids(kernel)
    if not runtime_ids:
        pytest.skip("live WARDEN declares no runtime identifiers")

    app = _live_app(tmp_path, monkeypatch, kernel)
    request_id = f"live-observation-{uuid4().hex[:8]}"
    with TestClient(app) as client:
        # The options endpoint surfaces the kernel-declared identifiers that
        # the plan compiler actually validates against.
        options = client.get("/v1/investigations/options", params={"force_refresh": "true"}).json()
        assert options["confirmed_runtime_ids"] == runtime_ids

        submission = client.post("/v1/investigations", json={
            "request_id": request_id,
            "contract_id": contracts[0],
            "model_id": runtime_ids[-1],
            "runtime_id": runtime_ids[0],
            "mode": "observation",
            "scenario_id": "release-family-live",
            "risk_type": "secret_leak",
            "start_run": True,
            "wait_for_completion": True,
            "timeout_seconds": 110,
            "poll_interval_seconds": 2.0,
        })
        assert submission.status_code == 200, submission.text
        result = submission.json()
        assert result["status"] == "completed"
        assert result["run"]["run_id"]

        # The graph is built from the real WARDEN run payload's message edges.
        graph = client.get(f"/v1/investigations/{request_id}/interaction-graph").json()
        assert graph["available"] is True
        assert graph["source"] == "warden_observation"
        assert graph["edges"], "real observation must yield message edges"
        for edge in graph["edges"]:
            assert edge["type"] == "message"
            assert edge["source"].startswith("agent:") and edge["target"].startswith("agent:")
            assert edge["message_id"]
        assert all("file:" not in ref for edge in graph["edges"] for ref in edge["evidence_refs"])

        # The rest of the C-round surface reflects the completed run.
        evidence = client.get(f"/v1/evidence/investigations/{request_id}").json()
        assert evidence["items"]
        experiment = client.get(f"/v1/experiments/{request_id}").json()
        assert experiment["execution_status"] == "completed"
        assert experiment["metrics_available"] is True
        timeline = client.get(f"/v1/investigations/{request_id}/timeline").json()
        assert "run_submission" in [event["event_type"] for event in timeline["items"]]
        assert "run_status_change" in [event["event_type"] for event in timeline["items"]]


def test_live_warden_c_round_readiness_options_graph_and_timeline(tmp_path, monkeypatch):
    kernel = _live_client()
    contracts = kernel.contracts()
    if not contracts:
        pytest.skip("The live WARDEN service has no threat contracts")
    app = _live_app(tmp_path, monkeypatch, kernel)

    with TestClient(app) as client:
        # C3 readiness against the real kernel: layer statuses must come from
        # the actual capability reports, not from health promotion.
        readiness = client.get("/v1/system/readiness", params={"force_refresh": "true"})
        assert readiness.status_code == 200
        checks = {check["name"]: check for check in readiness.json()["checks"]}
        assert checks["warden_connectivity"]["status"] == "available"
        assert checks["warden_api_compatibility"]["status"] == "available"
        assert checks["available_contracts"]["status"] == "available"
        assert checks["available_contracts"]["diagnostics"]["contract_count"] == len(contracts)
        runtime_report = next(
            (item for item in kernel.capabilities() if item.get("component_id") == "agent.runtime"),
            None,
        )
        runtime_available = bool(runtime_report and runtime_report.get("available") is True)
        assert checks["model_runtime"]["status"] == (
            "available" if runtime_available else "unavailable"
        )
        assert checks["intervention_execution"]["status"] == "unsupported"

        # C3 options must expose the real contract list verbatim.
        options = client.get("/v1/investigations/options", params={"force_refresh": "true"})
        assert options.status_code == 200
        body = options.json()
        assert body["threat_contracts"] == list(contracts)
        submittable_modes = [m["mode"] for m in body["modes"] if m["submittable"]]
        if not runtime_available:
            assert submittable_modes == []
            assert any(
                "COMPONENT_UNAVAILABLE:agent.runtime" in blocker
                for m in body["modes"] for blocker in m["blockers"]
            )

        # C4 timeline + C2 index before any submission.
        request_id = f"live-c-round-{uuid4().hex[:8]}"
        missing_graph = client.get(f"/v1/investigations/{request_id}/interaction-graph")
        assert missing_graph.status_code == 404

        # Real submission through the public API (plan only; no model runtime).
        submission = client.post("/v1/investigations", json={
            "request_id": request_id,
            "contract_id": contracts[0],
            "model_id": "live-c-round-smoke",
            "runtime_id": "live-c-round-smoke",
            "mode": "observation",
            "start_run": False,
        })
        assert submission.status_code == 200, submission.text
        result = submission.json()
        assert result["status"] in {"planned", "blocked", "completed", "unknown"}

        # The durable journal now backs the timeline and the experiment index.
        timeline = client.get(f"/v1/investigations/{request_id}/timeline")
        assert timeline.status_code == 200
        events = timeline.json()["items"]
        assert events[0]["event_type"] == "workflow_created"
        assert timeline.json()["workflow_status"] == result["workflow_status"]

        experiments = client.get("/v1/experiments").json()
        entry = next(
            (item for item in experiments["items"] if item["request_id"] == request_id), None,
        )
        assert entry is not None
        assert entry["data_source"] == "warden_workflow"
        assert entry["execution_status"] in {"planned", "blocked", "completed", "unknown"}
        detail = client.get(f"/v1/experiments/{request_id}").json()
        assert detail["request_id"] == request_id

        # C1 graph on a plan-only workflow: no run payload exists, so the graph
        # must report unavailable instead of inventing edges.
        graph = client.get(f"/v1/investigations/{request_id}/interaction-graph")
        assert graph.status_code == 200
        graph_body = graph.json()
        if result["status"] == "planned":
            assert graph_body["available"] is False
            assert graph_body["data_status"] == "unavailable"
            assert any("GRAPH_SOURCE_UNAVAILABLE" in note for note in graph_body["limitations"])
            assert graph_body["edges"] == []
