from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.mock import MockBackend
from agentguard.agents.coordinator import AgentCoordinator
from agentguard.agents.investigation_strategy import rank_findings
from agentguard.agents.role_permissions import authorize_role_tool
from agentguard.api.app import create_app
from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.hypothesis import InvestigationAction, InvestigationHypothesis
from agentguard.contracts.observation import DetectionFinding
from agentguard.core.orchestrator import AgentGuardEngine


def _candidate(finding_id, *, severity, score, evidence_refs, risk_type):
    return DetectionFinding(
        finding_id=finding_id,
        risk_type=risk_type,
        severity=severity,
        target_type="message",
        target_ref=f"target-{finding_id}",
        score=score,
        rationale="Candidate finding from an explicitly bounded observation trace.",
        evidence_refs=evidence_refs,
    )


def _experience(*, risk_type, verification_status="verified", outcome="success"):
    return ExperienceRecord(
        scenario_id="scenario-test",
        risk_type=risk_type,
        hypothesis_summary="Matched repeated test evidence supports this candidate.",
        action_type="block",
        action_summary="Proposed a scoped message-edge rule.",
        outcome=outcome,
        evidence_grade="interventional" if verification_status == "verified" else "observational",
        verification_status=verification_status,
        repetitions=2 if verification_status == "verified" else 1,
        evidence_refs=["artifact-a"],
        validation_notes=["Independent case-family repeats reviewed."] if verification_status == "verified" else [],
        allow_auto_recommendation=verification_status == "verified",
        created_at=datetime.now(UTC),
    )


def _rank_inputs():
    first = _candidate(
        "finding-first", severity="low", score=0.3, evidence_refs=["event-a"], risk_type="risk-a"
    )
    second = _candidate(
        "finding-second", severity="critical", score=0.95,
        evidence_refs=["event-b", "event-c"], risk_type="risk-b",
    )
    hypotheses = [
        InvestigationHypothesis(
            experiment_id="experiment-test", finding_id=first.finding_id,
            target_ref=first.target_ref, statement="Candidate A may indicate an unauthorized flow.",
            expected_effect="A scoped action should reduce the measured exposure.", confidence=0.3,
        ),
        InvestigationHypothesis(
            experiment_id="experiment-test", finding_id=second.finding_id,
            target_ref=second.target_ref, statement="Candidate B may indicate an unauthorized flow.",
            expected_effect="A scoped action should reduce the measured exposure.", confidence=0.95,
        ),
    ]
    actions = [
        InvestigationAction(
            hypothesis_id=hypotheses[0].hypothesis_id, target_type="message", target_ref=first.target_ref,
            operation="replace", expected_information_gain=0.8, estimated_cost=1.0,
            estimated_risk=0.1, utility_score=0.9, rationale="Higher estimated information value.",
        ),
        InvestigationAction(
            hypothesis_id=hypotheses[1].hypothesis_id, target_type="message", target_ref=second.target_ref,
            operation="block", expected_information_gain=0.2, estimated_cost=1.0,
            estimated_risk=0.1, utility_score=0.2, rationale="Lower estimated information value.",
        ),
    ]
    return [first, second], hypotheses, actions


def test_fixed_risk_and_information_strategies_can_rank_the_same_findings_differently():
    findings, hypotheses, actions = _rank_inputs()
    fixed, _ = rank_findings(findings, strategy="fixed_order")
    risk, _ = rank_findings(findings, strategy="risk_score")
    information, _ = rank_findings(
        findings, strategy="information_value", hypotheses=hypotheses, actions=actions
    )

    assert fixed[0].finding.finding_id == "finding-first"
    assert risk[0].finding.finding_id == "finding-second"
    assert information[0].finding.finding_id == "finding-first"
    assert information[0].rationale != risk[0].rationale


def test_experience_ranking_uses_only_verified_matching_records_and_falls_back_explicitly():
    findings, _, _ = _rank_inputs()
    history = [
        _experience(risk_type="risk-a"),
        _experience(risk_type="risk-b", verification_status="unverified", outcome="success"),
        _experience(risk_type="unrelated"),
    ]
    ranked, fallback = rank_findings(
        findings, strategy="experience", experience_records=history, scenario_id="scenario-test"
    )
    assert ranked[0].finding.finding_id == "finding-first"
    assert ranked[0].eligible_experience_count == 1
    assert ranked[1].eligible_experience_count == 0
    assert fallback is None

    ranked, fallback = rank_findings(
        findings,
        strategy="experience",
        experience_records=[_experience(risk_type="risk-a", verification_status="unverified")],
        scenario_id="scenario-test",
    )
    assert ranked[0].finding.finding_id == "finding-second"
    assert fallback == "no_eligible_verified_experience_fallback_to_risk_score"


def test_candidate_step_limit_and_decision_trace_are_bounded_and_auditable():
    findings, hypotheses, actions = _rank_inputs()
    coordinator = AgentCoordinator().new_run()
    coordinator.begin("run-strategy", "scenario-test", 1)
    selected = coordinator.investigate(
        findings,
        max_interventions=1,
        strategy="information_value",
        actions=actions,
        hypotheses=hypotheses,
        max_agent_steps=1,
        scenario_id="scenario-test",
    )
    assert selected is not None and selected.finding_id == "finding-first"
    assert coordinator.agent_steps_used == 1
    started = next(message for message in coordinator.messages if message.message_type == "strategy_started")
    assert started.payload["truncated_count"] == 1
    ranked = next(message for message in coordinator.messages if message.message_type == "candidate_ranked")
    chosen = next(message for message in coordinator.messages if message.message_type == "candidate_selected")
    assert ranked.evidence_refs == ["event-a"]
    assert chosen.payload["reason"]
    assert chosen.evidence_refs == ["event-a"]


def test_role_tool_allowlist_denies_unapproved_and_unknown_tools():
    authorize_role_tool("policy_engine", "evaluate_action")
    with pytest.raises(PermissionError, match="not allowed"):
        authorize_role_tool("trace_analyst", "execute_authorized_intervention")
    with pytest.raises(PermissionError, match="not allowed"):
        authorize_role_tool("unknown_role", "read_everything")


def test_request_and_budget_contracts_expose_strategy_and_agent_step_limit():
    request = ExperimentRequest.model_validate({
        "scenario_id": "scenario-test",
        "budget": {"max_agent_steps": 2},
        "investigation_strategy": "risk_score",
    })
    assert request.max_agent_steps == 2
    assert request.investigation_strategy == "risk_score"
    with pytest.raises(ValueError):
        ExperimentRequest.model_validate({"scenario_id": "scenario-test", "investigation_strategy": "unbounded"})


def test_experiment_design_tracks_the_finding_selected_by_the_configured_strategy():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(
        ExperimentRequest(
            scenario_id="demo_cloud_security",
            runtime_backend="mock",
            investigation_strategy="risk_score",
            max_agent_steps=2,
            auto_intervene=False,
        )
    )
    selected = next(
        message for message in report.agent_messages if message.message_type == "candidate_selected"
    )
    selected_hypothesis = next(
        hypothesis for hypothesis in report.hypotheses
        if hypothesis.finding_id == selected.payload["finding_id"]
    )
    assert report.experiment_designs
    assert report.experiment_designs[0].hypothesis_id == selected_hypothesis.hypothesis_id
    assert report.budget_usage is not None
    assert report.budget_usage.agent_steps_used <= report.budget_usage.max_agent_steps


def test_api_experience_strategy_reads_matching_verified_history(tmp_path):
    app = create_app(data_dir=tmp_path / "api-data")
    client = TestClient(app)
    base_request = {
        "scenario_id": "demo_cloud_security",
        "runtime_backend": "mock",
        "auto_intervene": False,
    }
    baseline = client.post("/v1/runs", json=base_request)
    assert baseline.status_code == 200, baseline.text
    baseline_finding = baseline.json()["findings"][0]
    app.state.experiences.add(ExperienceRecord(
        scenario_id="demo_cloud_security",
        risk_type=baseline_finding["risk_type"],
        hypothesis_summary="Controlled test fixture for experience-based ranking.",
        action_type="block",
        action_summary="Synthetic isolated-test policy candidate.",
        outcome="success",
        evidence_grade="interventional",
        verification_status="verified",
        repetitions=2,
        evidence_refs=["test-artifact-ref"],
        validation_notes=["Test fixture validates the query gate, not an experiment outcome."],
        allow_auto_recommendation=True,
    ))

    response = client.post(
        "/v1/runs",
        json={**base_request, "investigation_strategy": "experience"},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    matching_finding_ids = {
        finding["finding_id"]
        for finding in report["findings"]
        if finding["risk_type"] == baseline_finding["risk_type"]
    }
    ranked = [
        message for message in report["agent_messages"]
        if message["message_type"] == "candidate_ranked"
        and message["payload"]["finding_id"] in matching_finding_ids
    ]
    assert ranked
    assert ranked[0]["payload"]["eligible_experience_count"] == 1
