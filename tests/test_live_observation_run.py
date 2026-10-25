"""Live acceptance for the standardized observation workflow (D1).

Runs the fixed normal-observation configuration against a real WARDEN kernel
with a live model runtime. Skips honestly when the environment is missing;
a skip is never reported as a real run.
"""

import os

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient
from agentguard.api.app import create_app
from agentguard.contracts.run_config import InvestigationRunConfig
from agentguard.services.observation_workflow import ObservationRunService


def _live_kernel() -> WardenKernelClient:
    url = os.environ.get("AGENTGUARD_LIVE_WARDEN_URL") or os.environ.get("AGENTGUARD_WARDEN_URL")
    if not url:
        pytest.skip("AGENTGUARD_LIVE_WARDEN_URL is not configured")
    try:
        client = WardenKernelClient(url)
        assert client.health()["api_version"] == "v1"
        return client
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live WARDEN unreachable: {type(exc).__name__}")


def test_live_fixed_observation_run_config_end_to_end(tmp_path):
    kernel = _live_kernel()
    contracts = kernel.contracts()
    if not contracts:
        pytest.skip("the live WARDEN service has no threat contracts")

    app = create_app(data_dir=tmp_path / "data")
    service = ObservationRunService(
        client=kernel,
        workflows=app.state.warden_workflows,
        artifacts=app.state.artifacts,
        experiences=app.state.experiences,
    )
    declared = service.declared_runtime_ids()
    if not declared:
        pytest.skip("live WARDEN declares no runtime identifiers")

    # Fixed, repeatable configuration: identifiers come from the kernel's own
    # declarations, never hardcoded demo values.
    config = InvestigationRunConfig(
        run_config_id="fixed-observation-release-review",
        scenario_id="release-family-d1",
        contract_id=contracts[0],
        model_id=declared[-1],
        runtime_id=declared[0],
        audit_mode="observation",
        random_seed=7,
        investigation_budget=0,
        time_budget=110.0,
        model_call_budget=64,
        data_partition="held_out_family",
        source_commit=None,
        metadata={"purpose": "d1-fixed-acceptance"},
    )

    resolution = service.resolve(config)
    if resolution["status"] != "ready":
        blockers = ", ".join(resolution["blockers"])
        pytest.skip(f"live kernel cannot run the fixed config: {blockers}")

    record = service.execute(config)
    assert record.execution_status == "completed", record.blockers
    assert record.data_source == "warden_observation"
    assert record.run_id and record.plan_id
    assert record.evidence_refs
    assert all(not ref.lower().startswith(("file:", "\\\\", "/")) for ref in record.evidence_refs)
    assert record.submitted_at and record.completed_at
    assert record.run_config_artifact_id and record.record_artifact_id

    # Replay of the same fixed configuration is idempotent.
    replay = service.execute(config)
    assert replay.request_id == record.request_id
    assert any("Idempotent replay" in note for note in replay.limitations)

    # The full chain surfaces through the public C-round endpoints.
    with TestClient(app) as client:
        graph = client.get(
            f"/v1/investigations/{record.request_id}/interaction-graph"
        ).json()
        assert graph["available"] is True
        assert graph["source"] == "warden_observation"
        assert graph["edges"]
        evidence = client.get(
            f"/v1/evidence/investigations/{record.request_id}"
        ).json()
        assert evidence["items"]
        experiment = client.get(f"/v1/experiments/{record.request_id}").json()
        assert experiment["execution_status"] == "completed"
        timeline = client.get(
            f"/v1/investigations/{record.request_id}/timeline"
        ).json()
        assert "run_status_change" in [event["event_type"] for event in timeline["items"]]
