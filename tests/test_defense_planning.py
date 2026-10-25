"""Contract tests for evidence-constrained defense planning (D4)."""

import json

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.services.defense_planning import DefensePlanningService
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.warden_workflow_store import WardenWorkflowStore


def _workflow_entry(request_id="inv-defplan"):
    run = {
        "run_id": f"run-{request_id}", "plan_id": f"plan-{request_id}",
        "status": "completed",
        "evidence_bundle": {"items": [{
            "evidence_id": f"ev-{request_id}", "type": "finding",
            "evidence_level": "observational", "risk_type": "secret_flow",
            "target_type": "message", "target_ref": "message-17",
            "artifact_refs": ["warden-artifact-17"],
        }]},
    }
    return {
        "request_id": request_id, "status": "finished",
        "workflow_status": "completed", "run_id": run["run_id"],
        "request": {"scenario_id": "sc-def", "risk_type": "secret_flow",
                    "model_id": "m", "contract_id": "c", "runtime_id": "r",
                    "mode": "causal_audit"},
        "context": {"run": run, "plan": {"plan_id": run["plan_id"]}},
        "result": {"status": "completed", "run": run},
    }


def _seeded(tmp_path, request_id="inv-defplan"):
    workflows = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    entry = _workflow_entry(request_id)
    workflows.begin(request_id, {
        "request_id": request_id, "contract_id": "c", "model_id": "m",
        "runtime_id": "r", "mode": "causal_audit", "scenario_id": "sc-def",
        "risk_type": "secret_flow",
    })
    workflows.update(request_id, "finished", context=entry["context"],
                     result=entry["result"])
    return workflows, entry


def test_plan_generates_ranked_candidates_with_verification_plans(tmp_path):
    _workflows, entry = _seeded(tmp_path)
    service = DefensePlanningService(LocalArtifactStore(tmp_path / "artifacts"))
    plan = service.build_plan("inv-defplan", entry,
                              evidence_gateway=EvidenceGateway())
    assert plan.candidates
    for candidate in plan.candidates:
        assert candidate.policy_expression
        assert candidate.ranking.execution_supported is False
        assert candidate.ranking.unknown_fields  # no invented measurements
        assert candidate.verification_plan.execution_status == "not_executed"
        assert candidate.verification_plan.executability == (
            "blocked_no_intervention_executor"
        )
        assert "intervention.executor" in candidate.verification_plan.required_capabilities
        assert candidate.verification_plan.treatment_group
        assert candidate.verification_plan.control_group
    assert plan.overall_execution_supported is False
    assert plan.plan_artifact_id
    assert any("not evidence of effectiveness" in note for note in plan.limitations)


def test_missing_measurements_stay_unknown_not_invented(tmp_path):
    _workflows, entry = _seeded(tmp_path)
    service = DefensePlanningService(LocalArtifactStore(tmp_path / "artifacts"))
    plan = service.build_plan("inv-defplan", entry,
                              evidence_gateway=EvidenceGateway())
    for candidate in plan.candidates:
        ranking = candidate.ranking
        # execution_cost has no real measurement source: stays None and listed.
        assert ranking.execution_cost is None
        assert "execution_cost" in ranking.unknown_fields
        # evidence_sufficiency derives only from verified refs (none here).
        if ranking.evidence_sufficiency is not None:
            assert ranking.evidence_sufficiency <= 1.0


def test_empty_evidence_yields_empty_plan_not_fabrication(tmp_path):
    workflows = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    workflows.begin("inv-empty", {"request_id": "inv-empty"})
    service = DefensePlanningService(LocalArtifactStore(tmp_path / "artifacts"))
    plan = service.build_plan(
        "inv-empty", workflows.get("inv-empty"), evidence_gateway=EvidenceGateway(),
    )
    assert plan.candidates == []
    assert any("No governed evidence items" in note for note in plan.limitations)


def test_plan_artifact_contains_no_credentials_or_truth(tmp_path):
    _workflows, entry = _seeded(tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    service = DefensePlanningService(artifacts)
    plan = service.build_plan("inv-defplan", entry, evidence_gateway=EvidenceGateway())
    payload = artifacts.get_bytes(plan.plan_artifact_id).decode("utf-8")
    for banned in ("token", "secret", "ground_truth", "oracle"):
        assert banned not in payload.lower()
    assert "approval" not in payload.lower().replace("approval_required", "") \
        or "authorization" not in payload


def test_json_roundtrip_of_plan(tmp_path):
    _workflows, entry = _seeded(tmp_path)
    service = DefensePlanningService(LocalArtifactStore(tmp_path / "artifacts"))
    plan = service.build_plan("inv-defplan", entry, evidence_gateway=EvidenceGateway())
    restored = type(plan).model_validate(json.loads(plan.model_dump_json()))
    assert restored == plan.model_copy(update={"created_at": plan.created_at})
