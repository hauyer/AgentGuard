"""Controlled comparison of investigation strategies (D5).

Strategies run the same loop service over the same base investigations with
identical budgets, so comparisons are paired by construction. The evaluation
records exactly what ran: blocked/degraded runs count in the aggregates, and
metric groups without a legitimate source (risk-identification labels, real
interventions) stay null in the report contract.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.investigation_eval import (
    StrategyAggregate,
    StrategyComparisonReport,
    StrategyRunRecord,
)
from agentguard.contracts.investigation_loop import (
    InvestigationLoopBudget,
    InvestigationLoopReport,
)


class InvestigationEvaluationService:
    """Run and aggregate a fixed evaluation protocol."""

    def __init__(self, artifacts: LocalArtifactStore, *, source_commit: str | None = None):
        self.artifacts = artifacts
        self.source_commit = source_commit

    def run_comparison(
        self,
        *,
        loop_runner: Callable[..., InvestigationLoopReport],
        base_request_ids: list[str],
        strategies: list[str],
        repetitions: int,
        budget: InvestigationLoopBudget,
        data_partition: str = "held_out_family",
        comparison_id: str | None = None,
    ) -> StrategyComparisonReport:
        """Execute the protocol through the injected loop runner.

        ``loop_runner(base_request_id, strategy=..., budget=...)`` must be the
        real ``InvestigationLoopService.run``; injecting it keeps this service
        free of infrastructure wiring and lets tests use the real service over
        a seeded journal.
        """
        experiment_id = f"eval-{uuid.uuid4().hex[:10]}"
        runs: list[StrategyRunRecord] = []
        limitations: list[str] = [
            ("Strategies are compared on investigation behaviour over the same "
            "recorded evidence; this is not a model-capability comparison."),
            ("Risk-identification metrics require independent labels and are "
            "not computed (null); no detection accuracy is claimed."),
            ("Defense-effectiveness metrics require real interventions and are "
            "not computed (null)."),
        ]
        for base_request_id in base_request_ids:
            for strategy in strategies:
                for repetition in range(1, repetitions + 1):
                    record = self._run_one(
                        loop_runner, experiment_id, base_request_id,
                        strategy, repetition, budget,
                    )
                    runs.append(record)
        aggregates = [
            self._aggregate(strategy, [r for r in runs if r.strategy == strategy])
            for strategy in strategies
        ]
        if repetitions == 1:
            limitations.append(
                "Single repetition per cell: values are per-run observations "
                "with no dispersion estimate; treat differences as indicative."
            )
        report = StrategyComparisonReport(
            comparison_id=comparison_id or experiment_id,
            base_request_ids=base_request_ids,
            strategies=strategies,  # type: ignore[arg-type]
            repetitions=repetitions,
            data_partition=data_partition,  # type: ignore[arg-type]
            runs=runs,
            aggregates=aggregates,
            limitations=limitations,
            source_commit=self.source_commit,
        )
        return self._persist(report)

    def _run_one(
        self,
        loop_runner: Callable[..., InvestigationLoopReport],
        experiment_id: str,
        base_request_id: str,
        strategy: str,
        repetition: int,
        budget: InvestigationLoopBudget,
    ) -> StrategyRunRecord:
        base: dict[str, Any] = {
            "experiment_id": experiment_id,
            "run_label": f"{base_request_id}::{strategy}::r{repetition}",
            "strategy": strategy,
            "repetition": repetition,
            "base_request_id": base_request_id,
            "budget": budget,
            "limitations": [],
            "source_commit": self.source_commit,
        }
        try:
            report = loop_runner(
                base_request_id, strategy=strategy, budget=budget,
            )
        except Exception as exc:  # noqa: BLE001 - failures count, never hidden
            base.update(
                execution_status="failed",
                limitations=[f"loop raised {type(exc).__name__}; recorded as failure"],
            )
            return StrategyRunRecord(**base)
        metrics = report.metrics
        if report.strategy_degraded:
            base["limitations"].append(
                f"strategy degraded: {report.strategy_degradation_note}"
            )
        base.update(
            execution_status=(
                "completed" if report.phase != "BLOCKED" else "blocked"
            ),
            stop_reason=report.stop_reason,
            rounds_executed=metrics.rounds_executed,
            steps_to_first_evidence=metrics.steps_to_first_evidence,
            effective_action_ratio=metrics.effective_action_ratio,
            blocked_ratio=metrics.blocked_ratio,
            unsupported_ratio=metrics.unsupported_ratio,
            redundant_action_count=metrics.redundant_action_count,
            evidence_refs_collected=metrics.evidence_refs_collected,
            hypotheses_raised=metrics.hypotheses_raised,
            model_calls_used=metrics.model_calls_used,
            elapsed_seconds=metrics.elapsed_seconds,
            strategy_degraded=report.strategy_degraded,
            loop_report_artifact_id=report.report_artifact_id,
        )
        return StrategyRunRecord(**base)

    @staticmethod
    def _aggregate(strategy: str, runs: list[StrategyRunRecord]) -> StrategyAggregate:
        completed = [run for run in runs if run.execution_status == "completed"]
        notes: list[str] = []
        if not completed:
            notes.append("no completed runs; aggregates are null")

        def mean(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 3) if values else None

        return StrategyAggregate(
            strategy=strategy,  # type: ignore[arg-type]
            repetitions=len(runs),
            completed_runs=len(completed),
            blocked_runs=sum(run.execution_status == "blocked" for run in runs),
            mean_rounds=mean([run.rounds_executed for run in completed]),
            mean_steps_to_first_evidence=mean([
                float(run.steps_to_first_evidence)
                for run in completed if run.steps_to_first_evidence is not None
            ]),
            mean_effective_action_ratio=mean([
                run.effective_action_ratio for run in completed
                if run.effective_action_ratio is not None
            ]),
            mean_redundant_actions=mean([
                float(run.redundant_action_count) for run in completed
            ]),
            mean_evidence_refs=mean([
                float(run.evidence_refs_collected) for run in completed
            ]),
            mean_elapsed_seconds=mean([run.elapsed_seconds for run in completed]),
            degradation_rate=mean([float(run.strategy_degraded) for run in runs])
            if runs else None,
            notes=notes,
        )

    def _persist(self, report: StrategyComparisonReport) -> StrategyComparisonReport:
        artifact = self.artifacts.put(
            report.model_dump_json(indent=2).encode("utf-8"),
            name=f"strategy-comparison-{report.comparison_id}.json",
            description="Controlled investigation-strategy comparison record",
            tags=["strategy-comparison", report.data_partition],
            metadata={
                "comparison_id": report.comparison_id,
                "strategies": ",".join(report.strategies),
                "runs": len(report.runs),
            },
            media_type="application/json",
        )
        report.report_artifact_id = artifact.artifact_ref.artifact_id
        return report
