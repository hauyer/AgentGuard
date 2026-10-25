"""Live demonstration of multi-round real read-only investigation (D3).

Requires the live stack (WARDEN + model runtime) via
``AGENTGUARD_LIVE_WARDEN_URL``; executes the fixed observation run first and
then runs a read-only investigation loop over its real evidence, showing the
second round adapting to what the first round discovered.
"""

import os

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient
from agentguard.api.app import create_app
from agentguard.contracts.investigation_loop import InvestigationLoopBudget
from agentguard.contracts.run_config import InvestigationRunConfig
from agentguard.services.investigation_loop import InvestigationLoopService
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


def test_live_multi_round_read_only_investigation_adapts(tmp_path):
    kernel = _live_kernel()
    contracts = kernel.contracts()
    if not contracts:
        pytest.skip("the live WARDEN service has no threat contracts")

    app = create_app(data_dir=tmp_path / "data")
    observation = ObservationRunService(
        client=kernel,
        workflows=app.state.warden_workflows,
        artifacts=app.state.artifacts,
        experiences=app.state.experiences,
    )
    declared = observation.declared_runtime_ids()
    if not declared:
        pytest.skip("live WARDEN declares no runtime identifiers")

    # Step 1: the fixed real observation run supplies genuine evidence.
    config = InvestigationRunConfig(
        run_config_id="fixed-observation-loop-demo",
        scenario_id="release-family-d3",
        contract_id=contracts[0],
        model_id=declared[-1],
        runtime_id=declared[0],
        audit_mode="observation",
        random_seed=7,
        time_budget=110.0,
        metadata={"purpose": "d3-loop-demo"},
    )
    if observation.resolve(config)["status"] != "ready":
        pytest.skip("live kernel cannot run the fixed observation config")
    run_record = observation.execute(config)
    assert run_record.execution_status == "completed", run_record.blockers

    # Step 2: a read-only multi-round loop over that real workflow.
    loop = InvestigationLoopService(
        workflows=app.state.warden_workflows,
        artifacts=app.state.artifacts,
        evidence_gateway=app.state.evidence_gateway,
        experiment_index=app.state.experiment_index,
        risk_findings=app.state.risk_findings,
        experiences=app.state.experiences,
        observation=None,  # read-only demonstration
    )
    report = loop.run(
        run_record.request_id,
        strategy="information_value",
        budget=InvestigationLoopBudget(max_actions=6, no_new_evidence_rounds=3),
    )

    assert len(report.decisions) >= 2, "must demonstrate at least two rounds"
    selected = [decision.selected_action for decision in report.decisions]
    assert len(set(selected)) >= 2, "later rounds must adapt to earlier discoveries"
    first = report.decisions[0]
    second = report.decisions[1]
    assert first.selected_action != second.selected_action
    assert first.new_evidence or second.new_evidence
    assert report.metrics.steps_to_first_evidence is not None
    assert report.metrics.evidence_refs_collected > 0
    assert report.metrics.hypotheses_raised >= 0
    for decision in report.decisions:
        assert decision.result_status == "completed"
        assert decision.actual_cost >= 0
        assert decision.selection_reason
    assert report.stop_reason in {
        "action_budget_reached", "no_new_evidence", "time_budget_reached",
    }

    # The loop's evidence is real: the graph edges it discovered exist publicly.
    with TestClient(app) as client:
        graph = client.get(
            f"/v1/investigations/{run_record.request_id}/interaction-graph"
        ).json()
    assert graph["available"] is True and graph["edges"]
    discovered_edge_ids = {
        ref for decision in report.decisions for ref in decision.evidence_refs
    }
    assert any(edge["id"] in discovered_edge_ids for edge in graph["edges"])
