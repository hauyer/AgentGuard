"""Contract tests for controlled investigation-strategy evaluation (D5)."""

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.investigation_eval import StrategyComparisonReport
from agentguard.contracts.investigation_loop import InvestigationLoopBudget
from agentguard.memory.store import ExperienceStore
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.experiment_index import ExperimentIndexService
from agentguard.services.interaction_graph import InteractionGraphService
from agentguard.services.investigation_eval import InvestigationEvaluationService
from agentguard.services.investigation_loop import InvestigationLoopService
from agentguard.services.risk_findings import RiskFindingService
from agentguard.services.warden_workflow_store import WardenWorkflowStore


def _seed_base(workflows, request_id="inv-eval"):
    run = {
        "run_id": f"run-{request_id}", "plan_id": f"plan-{request_id}",
        "status": "completed",
        "evidence": {
            "findings": [{"kind": "observational_run", "messages": [
                {"message_id": "msg-1", "sender": "a", "recipient": "chair", "round_index": 0},
                {"message_id": "msg-2", "sender": "b", "recipient": "chair", "round_index": 0},
                {"message_id": "msg-3", "sender": "c", "recipient": "chair", "round_index": 0},
            ]}],
            "artifacts": [{"artifact_id": "art-" + "a" * 60}],
        },
    }
    workflows.begin(request_id, {
        "request_id": request_id, "contract_id": "contract-e", "model_id": "model-7b",
        "runtime_id": "stegopot.v1", "mode": "observation", "scenario_id": "sc-eval",
    })
    workflows.update(request_id, "finished", context={"run": run},
                     result={"status": "completed", "run": run})


def _loop(tmp_path):
    workflows = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    experiences = ExperienceStore(tmp_path / "exp.sqlite3")
    _seed_base(workflows)
    return InvestigationLoopService(
        workflows=workflows, artifacts=artifacts,
        evidence_gateway=EvidenceGateway(),
        experiment_index=ExperimentIndexService(workflows, artifacts),
        risk_findings=RiskFindingService(InteractionGraphService()),
        experiences=experiences, observation=None,
    ), artifacts


def test_comparison_runs_all_strategies_with_real_records(tmp_path):
    loop, artifacts = _loop(tmp_path)
    evaluation = InvestigationEvaluationService(artifacts, source_commit="test0001")
    report = evaluation.run_comparison(
        loop_runner=loop.run,
        base_request_ids=["inv-eval"],
        strategies=["fixed_order", "information_value"],
        repetitions=2,
        budget=InvestigationLoopBudget(max_actions=4, no_new_evidence_rounds=2),
    )
    assert isinstance(report, StrategyComparisonReport)
    assert len(report.runs) == 4  # 2 strategies x 2 repetitions; every run counts
    assert {run.strategy for run in report.runs} == {"fixed_order", "information_value"}
    for run in report.runs:
        assert run.experiment_id
        assert run.base_request_id == "inv-eval"
        assert run.budget.max_actions == 4
        assert run.source_commit == "test0001"
        assert run.execution_status in {"completed", "blocked", "failed"}
    # Same base + budget: paired by construction.
    fixed = [r for r in report.runs if r.strategy == "fixed_order"]
    info = [r for r in report.runs if r.strategy == "information_value"]
    assert len(fixed) == len(info) == 2
    assert report.report_artifact_id
    # Risk/defense metric groups stay null by contract.
    assert report.risk_identification_metrics is None
    assert report.defense_effectiveness_metrics is None


def test_aggregates_include_failures_and_degradations(tmp_path):
    loop, artifacts = _loop(tmp_path)

    def failing_runner(base_request_id, *, strategy, budget):
        if strategy == "information_value":
            raise RuntimeError("boom")
        return loop.run(base_request_id, strategy=strategy, budget=budget)

    evaluation = InvestigationEvaluationService(artifacts)
    report = evaluation.run_comparison(
        loop_runner=failing_runner,
        base_request_ids=["inv-eval"],
        strategies=["fixed_order", "information_value"],
        repetitions=2,
        budget=InvestigationLoopBudget(max_actions=3),
    )
    agg_info = next(a for a in report.aggregates if a.strategy == "information_value")
    assert agg_info.completed_runs == 0
    assert agg_info.mean_rounds is None
    assert any("no completed runs" in note for note in agg_info.notes)
    failed = [r for r in report.runs if r.strategy == "information_value"]
    assert all(r.execution_status == "failed" for r in failed)
    assert all("loop raised" in note for r in failed for note in r.limitations)


def test_single_repetition_flags_missing_dispersion(tmp_path):
    loop, artifacts = _loop(tmp_path)
    evaluation = InvestigationEvaluationService(artifacts)
    report = evaluation.run_comparison(
        loop_runner=loop.run,
        base_request_ids=["inv-eval"],
        strategies=["fixed_order"],
        repetitions=1,
        budget=InvestigationLoopBudget(max_actions=2),
    )
    assert any("Single repetition" in note for note in report.limitations)


def test_report_artifact_has_no_pre_filled_win_rates(tmp_path):
    loop, artifacts = _loop(tmp_path)
    evaluation = InvestigationEvaluationService(artifacts)
    report = evaluation.run_comparison(
        loop_runner=loop.run,
        base_request_ids=["inv-eval"],
        strategies=["fixed_order", "risk_score"],
        repetitions=1,
        budget=InvestigationLoopBudget(max_actions=3),
    )
    payload = artifacts.get_bytes(report.report_artifact_id).decode("utf-8").lower()
    for banned in ('"win_rate"', '"accuracy"', '"f1"', '"precision"', '"recall"'):
        assert banned not in payload
