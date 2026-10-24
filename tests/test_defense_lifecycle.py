import pytest

from agentguard.adapters.mock import MockBackend
from agentguard.contracts.defense import DefenseLifecycleRecord
from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.defense import DefensePlannerService, DefenseTransitionError
from agentguard.core.orchestrator import AgentGuardEngine


def _report():
    backend = MockBackend()
    return AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security",
        runtime_backend="mock",
        auto_intervene=False,
    ))


def test_defense_lifecycle_keeps_approval_execution_and_verification_separate():
    report = _report()
    candidate = DefensePlannerService().preview(report).candidates[0]
    service = DefensePlannerService()
    state = service.initial_lifecycle(candidate)
    for status in ("submitted", "executed", "verification_pending", "verified"):
        state = service.transition(state, status, f"test transition to {status}")

    assert state.status == "verified"
    assert [item.to_status for item in state.history] == [
        "proposed", "submitted", "executed", "verification_pending", "verified",
    ]
    with pytest.raises(DefenseTransitionError):
        service.transition(DefenseLifecycleRecord(candidate_id="other"), "verified", "illegal skip")


def test_defense_candidate_ranking_exposes_evidence_cost_and_verified_experience():
    report = _report()
    service = DefensePlannerService()
    baseline = service.preview(report)
    candidate = next(item for item in baseline.candidates if item.target_type == "message" and item.operation == "block")
    finding = next(item for item in report.findings if item.finding_id == candidate.finding_id)
    experience = ExperienceRecord(
        scenario_id=report.scenario_id,
        case_family="reviewed-family",
        risk_type=finding.risk_type,
        hypothesis_summary="Reviewed intervention action.",
        action_type="message_block",
        action_summary="Block this message class.",
        outcome="success",
        evidence_grade="interventional",
        verification_status="verified",
        repetitions=3,
        evidence_refs=["sha256:" + "a" * 64],
        validation_notes=["Independent paired repeats reviewed."],
        allow_auto_recommendation=True,
    )
    informed = service.preview(report, experiences=[experience])
    informed_candidate = next(item for item in informed.candidates if item.candidate_id == candidate.candidate_id)

    assert informed_candidate.evidence_level == "synthetic"
    assert informed_candidate.experience_refs == [experience.experience_id]
    assert informed_candidate.priority_score > candidate.priority_score
    assert any("cost" in reason.lower() for reason in informed_candidate.priority_reasons)
    assert any("reversible" in reason.lower() for reason in informed_candidate.priority_reasons)
    assert informed_candidate.status == "proposed"
