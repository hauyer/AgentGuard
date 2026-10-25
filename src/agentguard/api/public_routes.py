"""Stable business-facing API aliases over AgentGuard's established handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, Query

from agentguard.contracts.defense import DefensePreview
from agentguard.contracts.experiment_index import ExperimentCompareRequest
from agentguard.contracts.loop_public import InvestigationLoopCreateRequest
from agentguard.contracts.warden import WardenAuditRequest, WardenDefenseExecuteRequest


def create_public_router(
    *,
    health_handler: Callable[[], dict[str, Any]],
    capabilities_handler: Callable[[], dict[str, Any]],
    create_workflow_handler: Callable[[WardenAuditRequest], dict[str, Any]],
    list_workflows_handler: Callable[..., dict[str, Any]],
    get_workflow_handler: Callable[[str], dict[str, Any]],
    get_evidence_handler: Callable[[str], dict[str, Any]],
    build_defense_preview_handler: Callable[[str, bool], DefensePreview],
    execute_defense_handler: Callable[
        [str, WardenDefenseExecuteRequest, str | None], dict[str, Any]
    ],
    get_report_handler: Callable[[str], dict[str, Any]],
    get_interaction_graph_handler: Callable[..., dict[str, Any]],
    list_experiments_handler: Callable[..., dict[str, Any]],
    get_experiment_handler: Callable[[str], dict[str, Any]],
    compare_experiments_handler: Callable[[Any], dict[str, Any]],
    get_system_readiness_handler: Callable[..., dict[str, Any]],
    get_investigation_options_handler: Callable[..., dict[str, Any]],
    get_investigation_timeline_handler: Callable[..., dict[str, Any]],
    list_reports_handler: Callable[..., dict[str, Any]],
    get_risk_analysis_handler: Callable[..., dict[str, Any]],
    create_loop_handler: Callable[..., dict[str, Any]],
    list_loops_handler: Callable[..., dict[str, Any]],
    get_loop_handler: Callable[[str], dict[str, Any]],
    list_loop_decisions_handler: Callable[[str], dict[str, Any]],
    list_loop_evidence_changes_handler: Callable[[str], dict[str, Any]],
    get_loop_budget_handler: Callable[[str], dict[str, Any]],
) -> APIRouter:
    """Build stable aliases while keeping route definitions out of the app factory."""
    router = APIRouter()

    @router.get("/v1/system/health")
    def public_system_health() -> dict[str, Any]:
        return health_handler()

    @router.get("/v1/system/capabilities")
    def public_system_capabilities() -> dict[str, Any]:
        return capabilities_handler()

    @router.post("/v1/investigations")
    def create_public_investigation(request: WardenAuditRequest) -> dict[str, Any]:
        """Submit a governed WARDEN workflow through the public business API."""
        return create_workflow_handler(request)

    @router.post("/v1/workflows")
    def create_public_workflow(request: WardenAuditRequest) -> dict[str, Any]:
        return create_workflow_handler(request)

    @router.get("/v1/investigations")
    def list_public_investigations(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return list_workflows_handler(status=status, limit=limit, offset=offset)

    @router.get("/v1/workflows")
    def list_public_workflows(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return list_workflows_handler(status=status, limit=limit, offset=offset)

    @router.get("/v1/investigations/options")
    def get_public_investigation_options(
        force_refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Discovered audit modes, contracts and resources for the form."""
        return get_investigation_options_handler(force_refresh=force_refresh)

    @router.get("/v1/investigations/{request_id}/timeline")
    def get_public_investigation_timeline(
        request_id: str, after_event_id: int | None = Query(default=None, ge=1)
    ) -> dict[str, Any]:
        """Durable journal timeline with optional incremental fetch."""
        return get_investigation_timeline_handler(request_id, after_event_id)

    @router.get("/v1/investigations/{request_id}")
    def get_public_investigation(request_id: str) -> dict[str, Any]:
        return get_workflow_handler(request_id)

    @router.get("/v1/workflows/{request_id}")
    def get_public_workflow(request_id: str) -> dict[str, Any]:
        return get_workflow_handler(request_id)

    @router.get("/v1/investigations/{request_id}/evidence")
    def get_public_investigation_evidence(request_id: str) -> dict[str, Any]:
        return get_evidence_handler(request_id)

    @router.get("/v1/evidence/investigations/{request_id}")
    def get_public_evidence(request_id: str) -> dict[str, Any]:
        return get_evidence_handler(request_id)

    @router.get("/v1/defense-policies/{request_id}")
    def get_public_defense_policies(request_id: str) -> dict[str, Any]:
        return build_defense_preview_handler(request_id, False).model_dump(mode="json")

    @router.post("/v1/defense-policies/{request_id}/preview")
    def create_public_defense_policy_preview(request_id: str) -> dict[str, Any]:
        return build_defense_preview_handler(request_id, True).model_dump(mode="json")

    @router.post("/v1/defense-policies/{request_id}/execute")
    def execute_public_defense_policy(
        request_id: str,
        request: WardenDefenseExecuteRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict[str, Any]:
        return execute_defense_handler(request_id, request, x_approval_token)

    @router.get("/v1/investigations/{request_id}/risk-analysis")
    def get_public_risk_analysis(
        request_id: str, run_id: str | None = Query(default=None)
    ) -> dict[str, Any]:
        """Metadata-based risk findings and falsifiable hypotheses.

        Findings are structural signals over recorded interaction metadata.
        Uncalibrated scores are never attack probabilities, candidates are not
        causal claims, and missing evidence is reported as
        ``available=false`` / ``insufficient_evidence`` rather than fabricated.
        """
        return get_risk_analysis_handler(request_id, run_id)

    @router.post("/v1/investigation-loops")
    def create_public_investigation_loop(
        request: InvestigationLoopCreateRequest,
        x_agentguard_operator_token: str | None = Header(
            default=None, alias="X-AgentGuard-Operator-Token",
        ),
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
        x_loop_idempotency_key: str | None = Header(
            default=None, alias="X-Loop-Idempotency-Key",
        ),
    ) -> dict[str, Any]:
        """Run a multi-round investigation loop (operator/approval credential).

        Reads-only API tokens cannot create loops. Retries with the same
        ``X-Loop-Idempotency-Key`` and payload return the existing loop.
        """
        return create_loop_handler(
            request,
            x_agentguard_operator_token=x_agentguard_operator_token,
            x_approval_token=x_approval_token,
            x_loop_idempotency_key=x_loop_idempotency_key,
        )

    @router.get("/v1/investigation-loops")
    def list_public_investigation_loops(
        base_request_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Read-only loop registry view; authorization mirrors the base data."""
        return list_loops_handler(
            base_request_id=base_request_id, limit=limit, offset=offset,
        )

    @router.get("/v1/investigation-loops/{loop_id}")
    def get_public_investigation_loop(loop_id: str) -> dict[str, Any]:
        """One loop's summary with its persisted report provenance."""
        return get_loop_handler(loop_id)

    @router.get("/v1/investigation-loops/{loop_id}/decisions")
    def list_public_loop_decisions(loop_id: str) -> dict[str, Any]:
        """Per-round decisions from the persisted loop report; no retrofits."""
        return list_loop_decisions_handler(loop_id)

    @router.get("/v1/investigation-loops/{loop_id}/evidence-changes")
    def list_public_loop_evidence_changes(loop_id: str) -> dict[str, Any]:
        """Rounds that discovered new evidence."""
        return list_loop_evidence_changes_handler(loop_id)

    @router.get("/v1/investigation-loops/{loop_id}/budget")
    def get_public_loop_budget(loop_id: str) -> dict[str, Any]:
        """Budget envelope, consumption and remaining allowance."""
        return get_loop_budget_handler(loop_id)

    @router.get("/v1/reports")
    def list_public_reports(
        status: str | None = None,
        scenario_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Paginated index of persisted run reports with safe metadata."""
        return list_reports_handler(
            status=status, scenario_id=scenario_id, limit=limit, offset=offset,
        )

    @router.get("/v1/reports/{run_id}")
    def get_public_report(run_id: str) -> dict[str, Any]:
        return get_report_handler(run_id)

    @router.get("/v1/investigations/{request_id}/interaction-graph")
    def get_public_interaction_graph(
        request_id: str,
        run_id: str | None = Query(default=None),
        include_findings: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Message-level interaction graph with explicit availability and provenance.

        Real edges come only from message metadata that WARDEN's public run
        response provided. Without it the response reports ``available=false``;
        an explicit ``run_id`` renders the synthetic engine report through the
        same contract, always labelled ``agentguard_synthetic_report``. With
        ``include_findings=true`` metadata-based risk findings are mounted as
        ids on the recorded edges.
        """
        return get_interaction_graph_handler(
            request_id, run_id, include_findings,
        )

    @router.get("/v1/experiments")
    def list_public_experiments(
        status: str | None = None,
        scenario_id: str | None = None,
        data_source: str | None = Query(default=None, pattern="^(warden_workflow|synthetic_report)$"),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """Read-only experiment index over persisted workflows and run reports."""
        return list_experiments_handler(
            status=status, scenario_id=scenario_id, data_source=data_source,
            limit=limit, offset=offset,
        )

    @router.post("/v1/experiments/compare")
    def compare_public_experiments(request: ExperimentCompareRequest) -> dict[str, Any]:
        """Compare two existing run reports; incomparable pairs return not_comparable."""
        return compare_experiments_handler(request)

    @router.get("/v1/experiments/{experiment_id}")
    def get_public_experiment(experiment_id: str) -> dict[str, Any]:
        """Fetch one indexed experiment with only the metrics the source recorded."""
        return get_experiment_handler(experiment_id)

    @router.get("/v1/system/readiness")
    def get_public_system_readiness(
        force_refresh: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Per-layer readiness with sources, blockers and cache provenance."""
        return get_system_readiness_handler(force_refresh=force_refresh)

    return router
