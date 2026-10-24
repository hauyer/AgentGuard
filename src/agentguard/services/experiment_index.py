"""Read-only experiment index over already-persisted AgentGuard records.

Sources (in order of authority, without a second run database):

1. ``warden_workflow`` entries from the durable ``WardenWorkflowStore``
   journal (real WARDEN audit submissions).
2. ``synthetic_report`` entries from content-addressed ``run-report``
   artifacts written by the local research engine.

The index never invents metrics: unknown values stay ``None`` and missing
grouping stays ``experiment_id=None`` with an explicit limitation.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from agentguard.artifacts.local_store import ArtifactNotFoundError, LocalArtifactStore
from agentguard.contracts.artifacts import ArtifactMetadata
from agentguard.contracts.experiment_index import (
    ExperimentCompareResponse,
    ExperimentComparisonMetric,
    ExperimentDetail,
    ExperimentExecutionStatus,
    ExperimentListResponse,
    ExperimentMetrics,
    ExperimentSummary,
)
from agentguard.contracts.report import RunReport
from agentguard.services.warden_workflow_store import WardenWorkflowStore

logger = logging.getLogger(__name__)

_STATUS_MAP: dict[str, ExperimentExecutionStatus] = {
    "created": "created",
    "validating": "created",
    "ready": "planned",
    "planned": "planned",
    "blocked": "blocked",
    "queued": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "timed_out",
    "unknown": "unknown",
}
_ENGINE_STATUS_MAP: dict[str, ExperimentExecutionStatus] = {
    "completed": "completed", "partial": "partial", "failed": "failed",
}
_NO_GROUPING_NOTE = (
    "WARDEN workflow entries have no stable experiment grouping id in the "
    "current journal schema; their experiment_id stays null instead of being "
    "invented."
)
_ENGINE_METRICS_NOTE = (
    "metrics_available for engine reports is resolved by the detail endpoint; "
    "the list view does not read artifact contents."
)
_SYNTHETIC_NOTE = "Engine report metrics are synthetic demo measurements, not empirical results."
_DESCRIPTIVE_NOTE = (
    "This comparison is descriptive: without a recorded treatment/control "
    "pairing it cannot support a causal defense-effectiveness claim."
)


def _iso(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _bounded(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:512] if cleaned else None


class ExperimentIndexService:
    """Aggregate persisted workflows and run-report artifacts for the API."""

    def __init__(self, workflows: WardenWorkflowStore, artifacts: LocalArtifactStore) -> None:
        self.workflows = workflows
        self.artifacts = artifacts

    # ------------------------------------------------------------------ #
    # list
    # ------------------------------------------------------------------ #
    def list_experiments(
        self,
        *,
        status: str | None = None,
        scenario_id: str | None = None,
        data_source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ExperimentListResponse:
        summaries = [
            *self._workflow_summaries(),
            *self._artifact_summaries(),
        ]
        if status:
            summaries = [item for item in summaries if item.execution_status == status]
        if scenario_id:
            summaries = [item for item in summaries if item.scenario_id == scenario_id]
        if data_source:
            summaries = [item for item in summaries if item.data_source == data_source]
        summaries.sort(key=lambda item: (item.created_at or "", item.run_id or ""), reverse=True)
        total = len(summaries)
        page = summaries[offset:offset + limit]
        limitations = [_ENGINE_METRICS_NOTE]
        if any(item.data_source == "warden_workflow" and item.experiment_id is None for item in page):
            limitations.append(_NO_GROUPING_NOTE)
        return ExperimentListResponse(
            items=page, count=len(page), offset=offset, limit=limit,
            next_offset=offset + len(page) if len(page) == limit else None,
            total=total, limitations=limitations,
        )

    def _workflow_summaries(self) -> list[ExperimentSummary]:
        summaries: list[ExperimentSummary] = []
        page_offset = 0
        while True:
            entries = self.workflows.list(status=None, limit=500, offset=page_offset)
            if not entries:
                break
            for entry in entries:
                request = entry.get("request") if isinstance(entry.get("request"), Mapping) else {}
                workflow_status = str(entry.get("workflow_status") or "unknown")
                execution_status = _STATUS_MAP.get(workflow_status, "unknown")
                run = (entry.get("context") or {}).get("run") if isinstance(entry.get("context"), Mapping) else {}
                metrics_available = False
                if isinstance(run, Mapping):
                    evidence = run.get("evidence")
                    metrics_available = isinstance(evidence, Mapping) and bool(evidence.get("metrics"))
                summaries.append(ExperimentSummary(
                    experiment_id=None,
                    run_id=_bounded(entry.get("run_id")),
                    request_id=_bounded(entry.get("request_id")),
                    scenario_id=_bounded(request.get("scenario_id")),
                    model_id=_bounded(request.get("model_id")),
                    runtime_id=_bounded(request.get("runtime_id")),
                    mode=_bounded(request.get("mode")),
                    execution_status=execution_status,
                    raw_status=f"{entry.get('status', 'unknown')}/{workflow_status}",
                    data_source="warden_workflow",
                    evidence_level="observational" if execution_status == "completed" else "unknown",
                    created_at=_iso(entry.get("created_at")),
                    updated_at=_iso(entry.get("updated_at")),
                    metrics_available=metrics_available,
                ))
            if len(entries) < 500:
                break
            page_offset += 500
        return summaries

    def _artifact_summaries(self) -> list[ExperimentSummary]:
        summaries: list[ExperimentSummary] = []
        for metadata in self._run_report_artifacts():
            info = metadata.metadata
            status = str(info.get("status") or "unknown")
            created = metadata.artifact_ref.created_at.isoformat()
            summaries.append(ExperimentSummary(
                experiment_id=_bounded(info.get("experiment_id")),
                run_id=_bounded(info.get("run_id")),
                request_id=None,
                artifact_id=metadata.artifact_ref.artifact_id,
                scenario_id=_bounded(info.get("scenario_id")),
                model_id=None,
                runtime_id=None,
                mode=None,
                execution_status=_ENGINE_STATUS_MAP.get(status, "unknown"),
                raw_status=status,
                data_source="synthetic_report",
                evidence_level="synthetic",
                created_at=created,
                updated_at=created,
                metrics_available=False,
            ))
        return summaries

    def _run_report_artifacts(self) -> list[ArtifactMetadata]:
        return [
            item for item in self.artifacts.list_metadata()
            if "run-report" in item.tags
        ]

    # ------------------------------------------------------------------ #
    # detail
    # ------------------------------------------------------------------ #
    def get_experiment(self, key: str) -> ExperimentDetail | None:
        cleaned = key.strip()
        if not cleaned:
            return None
        for metadata in self._run_report_artifacts():
            info = metadata.metadata
            if info.get("experiment_id") == cleaned or info.get("run_id") == cleaned:
                report = self._load_report(metadata.artifact_ref.artifact_id)
                return self._artifact_detail(metadata, report)
        entry = self.workflows.get(cleaned)
        if entry is not None:
            return self._workflow_detail(entry)
        for summary in self._workflow_summaries():
            if summary.run_id == cleaned:
                entry = self.workflows.get(summary.request_id or "")
                if entry is not None:
                    return self._workflow_detail(entry)
        return None

    def _workflow_detail(self, entry: Mapping[str, Any]) -> ExperimentDetail:
        request = entry.get("request") if isinstance(entry.get("request"), Mapping) else {}
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        run = context.get("run") if isinstance(context.get("run"), Mapping) else {}
        workflow_status = str(entry.get("workflow_status") or "unknown")
        execution_status = _STATUS_MAP.get(workflow_status, "unknown")
        metrics: ExperimentMetrics | None = None
        limitations: list[str] = []
        if isinstance(run, Mapping):
            evidence = run.get("evidence") if isinstance(run.get("evidence"), Mapping) else {}
            raw_metrics = evidence.get("metrics") if isinstance(evidence.get("metrics"), Mapping) else {}
            source_metrics = {
                name: float(value) for name, value in raw_metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            if source_metrics:
                time_seconds = next(
                    (value for name, value in source_metrics.items()
                     if name.endswith("duration_seconds")),
                    None,
                )
                metrics = ExperimentMetrics(
                    source_metrics=source_metrics,
                    time_seconds=time_seconds,
                    notes=["Metrics are copied verbatim from the WARDEN run payload."],
                )
        if metrics is None:
            limitations.append("The recorded WARDEN run contains no measured metrics.")
        if execution_status != "completed":
            limitations.append(
                f"Workflow did not complete (status {execution_status}); "
                "no result metrics are expected."
            )
        return ExperimentDetail(
            experiment_id=None,
            run_id=_bounded(entry.get("run_id")),
            request_id=_bounded(entry.get("request_id")),
            scenario_id=_bounded(request.get("scenario_id")),
            model_id=_bounded(request.get("model_id")),
            runtime_id=_bounded(request.get("runtime_id")),
            mode=_bounded(request.get("mode")),
            execution_status=execution_status,
            raw_status=f"{entry.get('status', 'unknown')}/{workflow_status}",
            data_source="warden_workflow",
            evidence_level="observational" if execution_status == "completed" else "unknown",
            created_at=_iso(entry.get("created_at")),
            updated_at=_iso(entry.get("updated_at")),
            metrics_available=metrics is not None,
            metrics=metrics,
            limitations=limitations,
        )

    def _artifact_detail(
        self, metadata: ArtifactMetadata, report: RunReport | None
    ) -> ExperimentDetail:
        info = metadata.metadata
        status = str(info.get("status") or "unknown")
        created = metadata.artifact_ref.created_at.isoformat()
        metrics: ExperimentMetrics | None = None
        limitations: list[str] = [_SYNTHETIC_NOTE]
        if report is not None:
            notes: list[str] = []
            baseline = report.evaluation.baseline_metrics if report.evaluation else {}
            benign_utility = baseline["benign_utility"].value if "benign_utility" in baseline else None
            risk_exposure = baseline["risk_exposure"].value if "risk_exposure" in baseline else None
            model_tokens = report.budget_usage.model_tokens_used if report.budget_usage else None
            time_seconds = report.budget_usage.elapsed_seconds if report.budget_usage else None
            metrics_available = any(
                value is not None for value in (benign_utility, risk_exposure, model_tokens, time_seconds)
            )
            if metrics_available:
                metrics = ExperimentMetrics(
                    benign_utility=benign_utility,
                    risk_exposure=risk_exposure,
                    model_tokens=model_tokens,
                    time_seconds=time_seconds,
                    notes=notes,
                )
            else:
                limitations.append("The engine report contains no measured metrics.")
        else:
            limitations.append(
                "The run-report artifact could not be parsed; only index metadata is returned."
            )
        metrics_available = metrics is not None
        return ExperimentDetail(
            experiment_id=_bounded(info.get("experiment_id")),
            run_id=_bounded(info.get("run_id")),
            request_id=None,
            artifact_id=metadata.artifact_ref.artifact_id,
            scenario_id=_bounded(info.get("scenario_id")),
            model_id=_bounded(
                report.request_snapshot.model_version if report is not None and report.request_snapshot else None
            ),
            runtime_id=_bounded(
                report.request_snapshot.runtime_backend if report is not None and report.request_snapshot else None
            ),
            mode=None,
            execution_status=_ENGINE_STATUS_MAP.get(status, "unknown"),
            raw_status=status,
            data_source="synthetic_report",
            evidence_level="synthetic",
            created_at=created,
            updated_at=created,
            metrics_available=metrics_available,
            metrics=metrics,
            limitations=limitations,
        )

    def _load_report(self, artifact_id: str) -> RunReport | None:
        try:
            payload = json.loads(self.artifacts.get_bytes(artifact_id).decode("utf-8"))
        except (ArtifactNotFoundError, ValueError, UnicodeDecodeError, OSError) as exc:
            logger.warning("Run report artifact unreadable (%s)", type(exc).__name__)
            return None
        try:
            return RunReport.model_validate(payload)
        except ValueError:
            logger.warning("Run report artifact failed contract validation")
            return None

    # ------------------------------------------------------------------ #
    # compare
    # ------------------------------------------------------------------ #
    def compare(self, run_ids: list[str]) -> ExperimentCompareResponse:
        reasons: list[str] = []
        reports: dict[str, RunReport] = {}
        for run_id in run_ids:
            report = self._find_report(run_id)
            if report is None:
                reasons.append(f"RUN_REPORT_NOT_FOUND:{run_id}")
            else:
                reports[run_id] = report
        if reasons:
            return ExperimentCompareResponse(
                comparable=False, status="not_comparable", reasons=reasons, run_ids=run_ids,
            )

        first, second = run_ids
        report_a, report_b = reports[first], reports[second]
        if report_a.scenario_id != report_b.scenario_id:
            reasons.append(
                f"SCENARIO_MISMATCH:{report_a.scenario_id}!={report_b.scenario_id}"
            )
        if report_a.backend_mode != report_b.backend_mode:
            reasons.append(
                f"DATA_SOURCE_LEVEL_MISMATCH:{report_a.backend_mode}!={report_b.backend_mode}"
            )
        version_a = report_a.request_snapshot.model_version if report_a.request_snapshot else None
        version_b = report_b.request_snapshot.model_version if report_b.request_snapshot else None
        if version_a is None or version_b is None:
            reasons.append("MODEL_VERSION_NOT_RECORDED")
        elif version_a != version_b:
            reasons.append(f"MODEL_VERSION_MISMATCH:{version_a}!={version_b}")

        baseline_a = report_a.evaluation.baseline_metrics if report_a.evaluation else {}
        baseline_b = report_b.evaluation.baseline_metrics if report_b.evaluation else {}
        shared = sorted(set(baseline_a) & set(baseline_b))
        if not shared:
            reasons.append("NO_SHARED_METRICS")

        if reasons:
            return ExperimentCompareResponse(
                comparable=False, status="not_comparable", reasons=reasons, run_ids=run_ids,
            )

        metrics: list[ExperimentComparisonMetric] = []
        for name in shared:
            metric_a, metric_b = baseline_a[name], baseline_b[name]
            evidence_mode = (
                metric_a.evidence_mode if metric_a.evidence_mode == metric_b.evidence_mode else "observed"
            )
            metrics.append(ExperimentComparisonMetric(
                name=name, unit=metric_a.unit,
                values={first: metric_a.value, second: metric_b.value},
                difference=metric_b.value - metric_a.value,
                evidence_mode=evidence_mode,
            ))
        limitations = [_DESCRIPTIVE_NOTE]
        if report_a.backend_mode == "synthetic":
            limitations.append(_SYNTHETIC_NOTE)
        paired_link = any(
            execution.design_id in {
                item.design_id for item in report_b.paired_experiments
            }
            for execution in report_a.paired_experiments
        )
        if not paired_link:
            limitations.append(
                "No recorded treatment/control pairing links these runs; the side-by-side "
                "values are descriptive differences, not a defense-effect measurement."
            )
        return ExperimentCompareResponse(
            comparable=True, status="comparable", run_ids=run_ids,
            metrics=metrics, limitations=limitations,
        )

    def _find_report(self, run_id: str) -> RunReport | None:
        for metadata in self._run_report_artifacts():
            if metadata.metadata.get("run_id") == run_id:
                return self._load_report(metadata.artifact_ref.artifact_id)
        return None
