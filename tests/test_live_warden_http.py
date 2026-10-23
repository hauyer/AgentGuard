"""Opt-in HTTP test against an actual WARDEN process, never a fake transport."""

import os
from uuid import uuid4

import pytest

from agentguard.adapters.warden import WardenKernelClient
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.services.warden_lifecycle import WardenLifecycleService
from agentguard.services.warden_workflow_store import WardenWorkflowStore


def test_live_warden_preflight_plan_and_sqlite_journal(tmp_path):
    url = os.environ.get("AGENTGUARD_LIVE_WARDEN_URL")
    if not url:
        pytest.skip("AGENTGUARD_LIVE_WARDEN_URL is not configured")
    client = WardenKernelClient(url)
    health = client.health()
    assert health["api_version"] == "v1"
    assert isinstance(client.capabilities(), list)
    assert isinstance(client.resources(), list)
    contracts = client.contracts()
    if not contracts:
        pytest.skip("The live WARDEN service has no threat contracts")
    request = WardenAuditRequest(
        request_id=f"agentguard-live-{uuid4().hex}",
        contract_id=contracts[0],
        model_id="agentguard-http-contract-smoke",
        runtime_id="agentguard-http-contract-smoke",
        mode="capture_only",
        capture_targets=[{"target_id": "http-smoke-target", "agent_ids": ["receiver"], "layer": 0}],
        start_run=False,
    )
    journal = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    result = WardenLifecycleService(client, workflows=journal).execute(request)
    assert result["status"] in {"blocked", "planned"}
    if result["status"] == "blocked":
        assert result.get("blockers") or result["preflight"]["blockers"]
    assert "truth" not in (result.get("plan") or {}).get("threat_contract", {})
    assert journal.get(request.request_id)["status"] == "finished"
    assert journal.events(request.request_id)
