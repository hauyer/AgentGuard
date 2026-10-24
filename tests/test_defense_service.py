from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.defense import DefensePlannerService
from agentguard.core.orchestrator import AgentGuardEngine


def test_defense_preview_is_preview_only_and_paired_verification_is_explicit():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security", runtime_backend="mock", auto_intervene=False,
        max_interventions=2,
    ))
    service = DefensePlannerService()
    preview = service.preview(report)
    assert preview.execution_performed is False
    assert preview.candidates
    verification = service.verify_paired(
        report, preview.candidates[0], [0.9, 0.8, 0.95, 0.85], [0.2, 0.3, 0.2, 0.25],
        [0.95, 0.9, 0.96, 0.93], [0.91, 0.89, 0.94, 0.91],
    )
    assert verification.status == "passed"
    assert verification.risk_reduction > 0
    assert verification.rollback_tested is False


def test_defense_verification_is_inconclusive_without_benign_utility_pairs():
    backend = MockBackend()
    report = AgentGuardEngine(backend, backend, backend).run(ExperimentRequest(
        scenario_id="demo_cloud_security", runtime_backend="mock", auto_intervene=False,
        max_interventions=2,
    ))
    candidate = report.defense_preview.candidates[0]
    result = DefensePlannerService().verify_paired(
        report, candidate, [0.9, 0.8, 0.95, 0.85], [0.2, 0.3, 0.2, 0.25],
    )
    assert result.status == "inconclusive"
    assert result.benign_utility_before is None
