"""REST API for AgentGuard's controlled research and WARDEN workflows.

The built-in research engine is synthetic. Real audit execution enters through the
WARDEN client so this API does not become a second low-level model runtime.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import Field

from agentguard import __version__
from agentguard.adapters.mock import MockBackend
from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status
from agentguard.agents.coordinator import AgentCoordinator
from agentguard.agents.llm_advisor import LLMInvestigationAdvisor
from agentguard.artifacts.local_store import ArtifactNotFoundError, LocalArtifactStore
from agentguard.compat.linear_probe import (
    BinaryLinearProbe,
    select_f1_threshold,
    split_by_group_three_way,
)
from agentguard.compat.paired_runner import PairedExecutionUnsupported, PairedExperimentRunner
from agentguard.contracts.attribution import (
    AttributionNodeAnalysisReport,
    AttributionNodeAnalysisRequest,
    PathShiftAnalysisReport,
    PathShiftAnalysisRequest,
)
from agentguard.contracts.base import ContractModel
from agentguard.contracts.defense import DefenseCandidate, DefenseLifecycleRecord, DefensePreview
from agentguard.contracts.evidence import EvidenceRecord, GovernedEvidence
from agentguard.contracts.experience import (
    ExperienceRecommendationPolicy,
    ExperienceRecord,
    ExperienceSearchResult,
)
from agentguard.contracts.experience_bandit import ExperienceBanditRecommendationReport
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.intervention import InterventionRequest
from agentguard.contracts.lifecycle import RunTimelineEvent
from agentguard.contracts.paired import PairedExperimentRunRequest
from agentguard.contracts.probe import (
    BinaryProbePredictionReport,
    BinaryProbePredictRequest,
    BinaryProbeTrainingReport,
    BinaryProbeTrainRequest,
)
from agentguard.contracts.report import RunReport
from agentguard.contracts.research import (
    RegisteredStudy,
    StudyAnalysisReport,
    StudyAnalysisRequest,
    StudyRegistrationRequest,
)
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.core.attribution import analyze_attribution_node, analyze_path_shift
from agentguard.core.classification_metrics import evaluate_binary_classifier
from agentguard.core.defense import DefensePlannerService
from agentguard.core.experience_bandit import recommend_actions
from agentguard.core.graph import MultiLayerDetector
from agentguard.core.orchestrator import AgentGuardEngine
from agentguard.core.planner import DefensePlanner, InvestigationPlanner
from agentguard.core.policy import InterventionPolicyEngine
from agentguard.core.study_analysis import analyze_registered_study
from agentguard.memory.store import ExperienceStore
from agentguard.research_registry import ResearchRegistry
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.warden_diagnostics import diagnose_kernel
from agentguard.services.warden_lifecycle import WardenLifecycleService
from agentguard.services.warden_workflow_store import WardenWorkflowStore, WorkflowConflictError

logger = logging.getLogger(__name__)


class DefenseExecuteRequest(ContractModel):
    candidate_id: str
    authorization_ref: str | None = None
    intervention_id: str | None = None


class WardenDefenseExecuteRequest(ContractModel):
    candidate_id: str = Field(min_length=1, max_length=256)
    authorization_ref: str | None = Field(default=None, max_length=256)


class DefenseVerifyRequest(ContractModel):
    candidate_id: str
    baseline_risk: list[float] = Field(min_length=2)
    post_defense_risk: list[float] = Field(min_length=2)
    baseline_utility: list[float] | None = None
    post_defense_utility: list[float] | None = None
    source_artifact_refs: list[str] = Field(default_factory=list, max_length=32)
    seed: int = 7


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _configured_warden_client() -> WardenKernelClient | None:
    try:
        return WardenKernelClient.from_env()
    except WardenKernelError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc


def create_app(
    engine: AgentGuardEngine | None = None,
    *,
    data_dir: str | Path | None = None,
    paired_executor=None,
) -> FastAPI:
    if engine is not None and engine.runtime.capabilities().backend_mode == "real":
        raise ValueError(
            "Real audit runtimes must be accessed through the WARDEN kernel API; "
            "direct AgentGuard runtime injection is limited to synthetic research backends"
        )
    if paired_executor is not None:
        raise ValueError(
            "Direct paired execution is not a production AgentGuard API path; "
            "submit controlled audits through WARDEN"
        )

    try:
        rate_limit = int(os.environ.get("AGENTGUARD_RATE_LIMIT_PER_MINUTE", "300"))
    except ValueError as exc:
        raise ValueError("AGENTGUARD_RATE_LIMIT_PER_MINUTE must be a positive integer") from exc
    if rate_limit < 1:
        raise ValueError("AGENTGUARD_RATE_LIMIT_PER_MINUTE must be a positive integer")
    cors_origins = [
        origin.strip()
        for origin in os.environ.get("AGENTGUARD_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if "*" in cors_origins:
        raise ValueError("AGENTGUARD_CORS_ORIGINS must list explicit origins; wildcard is forbidden")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        executor = getattr(application.state, "executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="AgentGuard API",
        version=__version__,
        description=(
            "Controlled research API for multi-agent information-flow investigation. "
            "WARDEN workflows use a durable idempotency journal; the default research backend remains synthetic. "
            "A completed workflow is not, by itself, proof of causal validity."
        ),
        lifespan=lifespan,
    )
    if engine is None:
        backend = MockBackend()
        llm_enabled = os.environ.get("AGENTGUARD_LLM_INVESTIGATOR", "").strip().lower() in {"1", "true", "yes"}
        if llm_enabled:
            advisor = LLMInvestigationAdvisor.from_env()
            detector = MultiLayerDetector()
            investigator = InvestigationPlanner()
            defense_planner = DefensePlanner()
            coordinator = AgentCoordinator(
                detector=detector, investigator=investigator,
                defense_planner=defense_planner, advisor=advisor,
            )
            engine = AgentGuardEngine(
                backend, backend, backend, detector=detector, investigator=investigator,
                defense_planner=defense_planner, coordinator=coordinator,
            )
        else:
            engine = AgentGuardEngine(backend, backend, backend)
    app.state.engine = engine
    base_dir = Path(data_dir or os.environ.get("AGENTGUARD_DATA_DIR", ".agentguard")).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    app.state.reports: dict[str, RunReport] = {}
    app.state.jobs: dict[str, dict[str, Any]] = {}
    app.state.futures: dict[str, Future] = {}
    app.state.cancel_events: dict[str, Event] = {}
    app.state.run_artifacts: dict[str, str] = {}
    app.state.idempotency: dict[str, str] = {}
    app.state.run_locks: dict[str, RLock] = {}
    app.state.lock = RLock()
    app.state.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agentguard-run")
    app.state.artifacts = LocalArtifactStore(base_dir / "artifacts")
    app.state.experiences = ExperienceStore(base_dir / "experiences.sqlite3")
    app.state.warden_workflows = WardenWorkflowStore(base_dir / "warden_workflows.sqlite3")
    app.state.research_registry = ResearchRegistry(base_dir / "research_registry.sqlite3")
    app.state.defense_service = DefensePlannerService()
    app.state.evidence_gateway = EvidenceGateway()
    app.state.policy_engine = InterventionPolicyEngine()
    app.state.paired_runner = PairedExperimentRunner(app.state.engine.runtime, paired_executor=paired_executor)

    rate_lock = RLock()
    rate_windows: dict[str, deque[float]] = {}
    request_id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization", "Content-Type", "X-AgentGuard-Token",
                "X-AgentGuard-Operator-Token", "X-Approval-Token", "X-Request-ID",
            ],
        )

    @app.middleware("http")
    async def api_security_middleware(request: Request, call_next):
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_id if request_id_pattern.fullmatch(supplied_id) else str(uuid.uuid4())
        request.state.request_id = request_id
        api_token = os.environ.get("AGENTGUARD_API_TOKEN", "")
        operator_token = os.environ.get("AGENTGUARD_OPERATOR_API_TOKEN", "")
        supplied_api = request.headers.get("X-AgentGuard-Token", "")
        supplied_operator = request.headers.get("X-AgentGuard-Operator-Token", "")
        api_valid = bool(api_token and supplied_api and secrets.compare_digest(api_token, supplied_api))
        operator_valid = bool(
            operator_token and supplied_operator and secrets.compare_digest(operator_token, supplied_operator)
        )
        legacy_operator_valid = bool(
            not operator_token and api_token and supplied_api and secrets.compare_digest(api_token, supplied_api)
        )
        actor_role = "operator" if operator_valid or legacy_operator_valid else ("api" if api_valid else "anonymous")
        public_paths = {"/health", "/v1/system/health", "/openapi.json", "/docs", "/redoc", "/console"}
        auth_denied = (
            request.method != "OPTIONS"
            and (api_token or operator_token)
            and request.url.path not in public_paths
            and not (api_valid or operator_valid)
        )
        host = request.client.host if request.client else "unknown"
        now = time.monotonic()
        throttled = False
        retry_after = 0
        if request.method != "OPTIONS":
            with rate_lock:
                window = rate_windows.setdefault(host, deque())
                while window and now - window[0] >= 60:
                    window.popleft()
                throttled = len(window) >= rate_limit
                if throttled:
                    retry_after = max(1, int(60 - (now - window[0])))
                else:
                    window.append(now)
        if throttled:
            response: Response = JSONResponse(
                status_code=429,
                content={"detail": {"error": "RATE_LIMIT_EXCEEDED", "request_id": request_id}},
                headers={"Retry-After": str(retry_after)},
            )
        elif auth_denied:
            response = JSONResponse(
                status_code=401,
                content={"detail": {"error": "API_TOKEN_REQUIRED", "request_id": request_id}},
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("Unhandled AgentGuard API error (request_id=%s)", request_id)
                response = JSONResponse(
                    status_code=500,
                    content={"detail": {"error": "INTERNAL_SERVER_ERROR", "request_id": request_id}},
                )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        _record_api_audit(request, request_id, response.status_code, actor_role)
        return response

    def _record_api_audit(request: Request, request_id: str, status_code: int, actor_role: str) -> None:
        try:
            app.state.warden_workflows.record_api_audit_event(
                request_id=request_id,
                method=request.method,
                path=getattr(request.scope.get("route"), "path", "/<unmatched>"),
                status_code=status_code,
                actor_role=actor_role,
            )
        except Exception:
            # The request path and response remain usable, while audit sink failures are visible to operators.
            logger.exception("AgentGuard API audit event could not be persisted (request_id=%s)", request_id)

    def persist_report(report: RunReport) -> str:
        payload = report.model_dump_json(indent=2).encode("utf-8")
        metadata = app.state.artifacts.put(
            payload,
            name=f"run-report-{report.run_id}.json",
            description="Content-addressed AgentGuard run report",
            tags=["run-report", report.backend_mode, report.status],
            metadata={
                "run_id": report.run_id,
                "experiment_id": report.experiment_id,
                "scenario_id": report.scenario_id,
                "status": report.status,
                "backend_mode": report.backend_mode,
            },
            media_type="application/json",
        )
        with app.state.lock:
            app.state.run_artifacts[report.run_id] = metadata.artifact_ref.artifact_id
        return metadata.artifact_ref.artifact_id

    def store_report(report: RunReport, alias: str | None = None) -> None:
        persist_report(report)
        with app.state.lock:
            app.state.reports[report.run_id] = report
            if alias:
                app.state.reports[alias] = report

    def get_report_or_404(run_id: str) -> RunReport:
        with app.state.lock:
            report = app.state.reports.get(run_id)
            job = app.state.jobs.get(run_id)
        if report is not None:
            return report
        if job is not None and job.get("status") in {"queued", "running", "cancel_requested"}:
            raise HTTPException(status_code=409, detail={"error": "RUN_NOT_FINISHED", "job": job})
        if job is not None and job.get("status") == "failed":
            raise HTTPException(status_code=500, detail={"error": "RUN_FAILED", "job_id": run_id})
        if job is not None and job.get("status") == "cancelled":
            report = app.state.reports.get(run_id)
            if report:
                return report
        raise HTTPException(status_code=404, detail="run_id not found")

    def _run_job(job_id: str, request: ExperimentRequest, cancel_event: Event) -> None:
        with app.state.lock:
            if cancel_event.is_set():
                app.state.jobs[job_id].update(status="cancelled", updated_at=_utcnow())
                return
            app.state.jobs[job_id].update(status="running", updated_at=_utcnow(), started_at=_utcnow())
        try:
            report = run_engine(request, cancel_event=cancel_event)
            store_report(report, alias=job_id)
            status = "cancelled" if cancel_event.is_set() and report.status == "partial" else report.status
            with app.state.lock:
                app.state.jobs[job_id].update(
                    status=status,
                    updated_at=_utcnow(),
                    completed_at=_utcnow(),
                    result_run_id=report.run_id,
                    artifact_id=app.state.run_artifacts.get(report.run_id),
                    report_status=report.status,
                )
        except Exception as exc:  # noqa: BLE001 - do not leak config values or raw exception text
            with app.state.lock:
                app.state.jobs[job_id].update(
                    status="failed",
                    updated_at=_utcnow(),
                    completed_at=_utcnow(),
                    error_code=type(exc).__name__,
                )

    def run_engine(request: ExperimentRequest, *, cancel_event: Event | None = None) -> RunReport:
        experiences = None
        experience_policy = None
        if request.investigation_strategy == "experience":
            experiences = app.state.experiences.recommendations(
                scenario_id=request.scenario_id,
                case_family=request.case_family,
                model_version=request.model_version,
                policy_version=request.experience_record_policy_version,
                limit=500,
            )
            try:
                experience_policy = app.state.experiences.policy(request.recommendation_policy_version)
            except KeyError as exc:
                raise ValueError("recommendation_policy_version not found") from exc
        return app.state.engine.run(
            request,
            cancel_event=cancel_event,
            experience_records=experiences,
            experience_policy=experience_policy,
        )

    def approval_is_valid(token: str | None) -> bool:
        expected = os.environ.get("AGENTGUARD_APPROVAL_TOKEN", "")
        return bool(expected and token and secrets.compare_digest(expected, token))

    def require_operator_token(token: str | None) -> None:
        """Require the operator credential, with an explicit legacy API-token fallback."""
        expected = os.environ.get("AGENTGUARD_OPERATOR_API_TOKEN") or os.environ.get("AGENTGUARD_API_TOKEN", "")
        if not expected or not token or not secrets.compare_digest(expected, token):
            raise HTTPException(status_code=403, detail={"error": "OPERATOR_TOKEN_REQUIRED"})

    def resolve_job_status(run_id: str) -> dict[str, Any] | None:
        with app.state.lock:
            job = app.state.jobs.get(run_id)
            return dict(job) if job is not None else None

    def get_run_lock(run_id: str) -> RLock:
        with app.state.lock:
            return app.state.run_locks.setdefault(run_id, RLock())

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "agentguard", "version": __version__}

    @app.get("/v1/system/health")
    def public_system_health() -> dict:
        return health()

    @app.get("/console", response_class=HTMLResponse)
    def console() -> str:
        return CONSOLE_HTML

    @app.get("/v1/agents/status")
    def get_agent_status() -> dict:
        configured = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        return {
            "llm_investigator_enabled": os.environ.get("AGENTGUARD_LLM_INVESTIGATOR", "").strip().lower() in {"1", "true", "yes"},
            "credentials_configured": configured,
            "provider_base_url": os.environ.get("AGENTGUARD_LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")),
            "model": os.environ.get("AGENTGUARD_LLM_MODEL", "deepseek-chat"),
            "network_call_performed": False,
            "authority": "candidate prioritization only; no tools, approval authority, or direct execution",
        }

    @app.get("/v1/capabilities")
    def get_capabilities() -> dict:
        manifest = app.state.engine.runtime.capabilities()
        return manifest.model_dump(mode="json")

    @app.get("/v1/system/capabilities")
    def public_system_capabilities() -> dict:
        return get_capabilities()

    @app.get("/v1/system/audit-events")
    def list_api_audit_events(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        x_agentguard_operator_token: str | None = Header(default=None, alias="X-AgentGuard-Operator-Token"),
        x_agentguard_token: str | None = Header(default=None, alias="X-AgentGuard-Token"),
    ) -> dict:
        require_operator_token(x_agentguard_operator_token or x_agentguard_token)
        items = app.state.warden_workflows.list_api_audit_events(limit=limit, offset=offset)
        return {"items": items, "count": len(items), "offset": offset, "limit": limit}

    @app.get("/v1/kernel/status")
    def get_kernel_status() -> dict:
        """Report WARDEN integration state without treating missing infrastructure as low risk."""
        return warden_status()

    @app.get("/v1/kernel/doctor")
    def get_kernel_doctor(
        contract_id: str | None = None,
        probe_id: str | None = None,
        sae_id: str | None = None,
        mode: Literal[
            "observation", "capture_only", "detection", "causal_audit", "defense_evaluation"
        ] = "causal_audit",
    ) -> dict:
        return diagnose_kernel(
            _configured_warden_client(),
            contract_id=contract_id,
            probe_id=probe_id,
            sae_id=sae_id,
            mode=mode,
        )

    @app.get("/v1/kernel/capabilities")
    def get_kernel_capabilities() -> dict:
        """Proxy WARDEN capability reports; no local capabilities are inferred."""
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return {"kernel": "WARDEN", "items": client.capabilities()}
        except WardenKernelError as exc:
            raise HTTPException(status_code=502, detail={"error": exc.code, "message": str(exc)}) from exc

    @app.get("/v1/kernel/resources")
    def get_kernel_resources(kind: str | None = Query(default=None, pattern="^(probe|sae)$")) -> dict:
        """Proxy authoritative Probe/SAE resource manifests from WARDEN."""
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return {"kernel": "WARDEN", "items": client.resources(kind)}
        except WardenKernelError as exc:
            raise HTTPException(status_code=502, detail={"error": exc.code, "message": str(exc)}) from exc

    @app.get("/v1/kernel/contracts")
    def get_kernel_contracts() -> dict:
        """Proxy threat-contract IDs available to WARDEN."""
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return {"kernel": "WARDEN", "items": client.contracts()}
        except WardenKernelError as exc:
            raise HTTPException(status_code=502, detail={"error": exc.code, "message": str(exc)}) from exc

    @app.post("/v1/kernel/audits")
    def submit_kernel_audit(request: WardenAuditRequest) -> dict:
        """Compatibility alias using the same durable workflow and approval gate."""
        result = execute_kernel_workflow(request)
        return {**result, "submission_status": result["status"]}

    @app.get("/v1/kernel/audits/{plan_id}")
    def get_kernel_audit_plan(plan_id: str) -> dict:
        """Read the authoritative WARDEN audit plan and its blockers."""
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return {"kernel": "WARDEN", "plan": client.get_audit_plan(plan_id)}
        except WardenKernelError as exc:
            status_code = 404 if exc.status_code == 404 else 502
            raise HTTPException(status_code=status_code, detail={"error": exc.code, "message": str(exc)}) from exc

    @app.get("/v1/kernel/runs/{run_id}")
    def get_kernel_run(run_id: str) -> dict:
        """Read the authoritative WARDEN run status and evidence references."""
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return {"kernel": "WARDEN", "run": client.get_run(run_id)}
        except WardenKernelError as exc:
            status_code = 404 if exc.status_code == 404 else 502
            raise HTTPException(status_code=status_code, detail={"error": exc.code, "message": str(exc)}) from exc

    @app.post("/v1/kernel/workflows")
    def execute_kernel_workflow(request: WardenAuditRequest) -> dict:
        """Preflight, submit and optionally wait for a WARDEN audit; persist evidence-gated memory."""
        if request.mode == "defense_evaluation" and not approval_is_valid(request.authorization_ref):
            raise HTTPException(
                status_code=403,
                detail={"error": "OPERATOR_APPROVAL_REQUIRED", "message": "defense_evaluation requires a valid AgentGuard approval reference"},
            )
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            service = WardenLifecycleService(
                client,
                experiences=app.state.experiences,
                workflows=app.state.warden_workflows,
            )
            result = service.execute(request, approval_valid=True)
            return result
        except WorkflowConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "IDEMPOTENCY_KEY_CONFLICT", "message": str(exc)},
            ) from exc
        except WardenKernelError as exc:
            status_code = 502 if exc.status_code is None or exc.status_code >= 500 else 400
            raise HTTPException(
                status_code=status_code,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc

    @app.get("/v1/kernel/workflows")
    def list_kernel_workflows(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        """List durable AgentGuard workflow summaries for operational reconciliation."""
        entries = app.state.warden_workflows.list(status=status, limit=limit, offset=offset)
        return {
            "items": [
                {
                    "request_id": entry["request_id"],
                    "status": entry["status"],
                    "workflow_status": entry["workflow_status"],
                    "revision": entry["revision"],
                    "created_at": entry["created_at"],
                    "updated_at": entry["updated_at"],
                    "plan_id": (entry.get("context") or {}).get("plan", {}).get("plan_id"),
                    "run_id": (entry.get("context") or {}).get("run", {}).get("run_id"),
                    "error_code": entry.get("error_code"),
                }
                for entry in entries
            ],
            "count": len(entries),
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(entries) if len(entries) == limit else None,
        }

    @app.post("/v1/investigations")
    def create_public_investigation(request: WardenAuditRequest) -> dict:
        """Stable business alias for submitting an AgentGuard-orchestrated WARDEN workflow."""
        return execute_kernel_workflow(request)

    @app.post("/v1/workflows")
    def create_public_workflow(request: WardenAuditRequest) -> dict:
        return execute_kernel_workflow(request)

    @app.get("/v1/investigations")
    def list_public_investigations(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        return list_kernel_workflows(status=status, limit=limit, offset=offset)

    @app.get("/v1/workflows")
    def list_public_workflows(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        return list_kernel_workflows(status=status, limit=limit, offset=offset)

    @app.post("/v1/kernel/workflows/{request_id}/cancel")
    def cancel_pending_kernel_workflow(request_id: str) -> dict:
        """Cancel unsubmitted local work; never claim remote model cancellation."""
        try:
            return WardenLifecycleService.cancel_pending(app.state.warden_workflows, request_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"}) from exc

    @app.get("/v1/kernel/workflows/{request_id}")
    def get_kernel_workflow(request_id: str) -> dict:
        """Inspect a durable upper-layer workflow record without contacting WARDEN."""
        entry = app.state.warden_workflows.get(request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})
        return {
            "request_id": entry["request_id"],
            "status": entry["status"],
            "workflow_status": entry["workflow_status"],
            "revision": entry["revision"],
            "statistics": app.state.warden_workflows.execution_statistics(request_id),
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
            "plan_id": (entry.get("context") or {}).get("plan", {}).get("plan_id"),
            "run_id": (entry.get("context") or {}).get("run", {}).get("run_id"),
            "result": entry.get("result"),
            "error_code": entry.get("error_code"),
            "error_message": entry.get("error_message"),
        }

    @app.get("/v1/kernel/workflows/{request_id}/evidence")
    def get_kernel_workflow_evidence(request_id: str) -> dict:
        """Return a governed summary of journaled WARDEN evidence without opening its URIs."""
        entry = app.state.warden_workflows.get(request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})
        return app.state.evidence_gateway.for_warden_workflow(
            entry,
            artifacts=app.state.artifacts,
        )

    @app.get("/v1/investigations/{request_id}")
    def get_public_investigation(request_id: str) -> dict:
        return get_kernel_workflow(request_id)

    @app.get("/v1/workflows/{request_id}")
    def get_public_workflow(request_id: str) -> dict:
        return get_kernel_workflow(request_id)

    @app.get("/v1/investigations/{request_id}/evidence")
    def get_public_investigation_evidence(request_id: str) -> dict:
        return get_kernel_workflow_evidence(request_id)

    @app.get("/v1/evidence/investigations/{request_id}")
    def get_public_evidence(request_id: str) -> dict:
        return get_kernel_workflow_evidence(request_id)

    def _build_warden_defense_preview(request_id: str, *, persist: bool) -> DefensePreview:
        entry = app.state.warden_workflows.get(request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})
        governed = app.state.evidence_gateway.for_warden_workflow(
            entry,
            artifacts=app.state.artifacts,
        )
        items = [GovernedEvidence.model_validate(item) for item in governed["items"]]
        experience_records: dict[str, ExperienceRecord] = {}
        for item in items:
            if item.scenario_id == "unknown" or item.risk_type == "unknown":
                continue
            for record in app.state.experiences.recommendations(
                scenario_id=item.scenario_id,
                risk_type=item.risk_type,
                limit=500,
            ):
                experience_records[record.experience_id] = record
        preview = app.state.defense_service.preview_from_evidence(
            run_id=str(entry.get("run_id") or request_id),
            evidence=items,
            run_status=governed["run_status"],
            experiences=experience_records.values(),
        )
        if persist and preview.candidates:
            app.state.warden_workflows.save_defense_candidates(
                request_id,
                [
                    (
                        candidate.model_dump(mode="json"),
                        app.state.defense_service.initial_lifecycle(candidate).model_dump(mode="json"),
                    )
                    for candidate in preview.candidates
                ],
            )
        for candidate in preview.candidates:
            saved = app.state.warden_workflows.get_defense_candidate(request_id, candidate.candidate_id)
            if saved is not None:
                candidate.status = saved["lifecycle"]["status"]
        return preview

    @app.get("/v1/kernel/workflows/{request_id}/defenses/preview")
    def get_warden_defense_preview(request_id: str) -> dict:
        return _build_warden_defense_preview(request_id, persist=False).model_dump(mode="json")

    @app.post("/v1/kernel/workflows/{request_id}/defenses/preview")
    def create_warden_defense_preview(request_id: str) -> dict:
        return _build_warden_defense_preview(request_id, persist=True).model_dump(mode="json")

    @app.get("/v1/defense-policies/{request_id}")
    def get_public_defense_policies(request_id: str) -> dict:
        return _build_warden_defense_preview(request_id, persist=False).model_dump(mode="json")

    @app.post("/v1/defense-policies/{request_id}/preview")
    def create_public_defense_policy_preview(request_id: str) -> dict:
        return _build_warden_defense_preview(request_id, persist=True).model_dump(mode="json")

    @app.post("/v1/kernel/workflows/{request_id}/defenses/execute")
    def execute_warden_defense_proposal(
        request_id: str,
        request: WardenDefenseExecuteRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        if not request.authorization_ref:
            raise HTTPException(status_code=400, detail={"error": "AUTHORIZATION_REFERENCE_REQUIRED"})
        saved = app.state.warden_workflows.get_defense_candidate(request_id, request.candidate_id)
        if saved is None:
            raise HTTPException(status_code=404, detail={"error": "DEFENSE_CANDIDATE_NOT_FOUND"})
        candidate = DefenseCandidate.model_validate(saved["candidate"])
        lifecycle = DefenseLifecycleRecord.model_validate(saved["lifecycle"])
        if lifecycle.status == "unsupported":
            raise HTTPException(status_code=501, detail={
                "error": "WARDEN_DEFENSE_EXECUTOR_UNSUPPORTED",
                "defense_status": lifecycle.status,
                "message": "No declared external policy executor is available; no defense action was submitted.",
            })
        if not approval_is_valid(x_approval_token):
            if lifecycle.status == "proposed":
                lifecycle = app.state.defense_service.transition(
                    lifecycle,
                    "awaiting_approval",
                    "An operator approval token is required before requesting execution.",
                )
                saved = app.state.warden_workflows.update_defense_candidate(
                    request_id,
                    candidate.candidate_id,
                    candidate.model_dump(mode="json"),
                    lifecycle.model_dump(mode="json"),
                )
            raise HTTPException(status_code=403, detail={
                "error": "APPROVAL_REQUIRED",
                "defense_status": saved["lifecycle"]["status"],
                "message": "No defense action was submitted; provide the configured operator approval token to continue.",
            })
        lifecycle = app.state.defense_service.transition(
            lifecycle,
            "approved",
            "The configured operator approval token was accepted for this proposal.",
        )
        saved = app.state.warden_workflows.update_defense_candidate(
            request_id,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
            lifecycle.model_dump(mode="json"),
        )
        lifecycle = app.state.defense_service.transition(
            lifecycle,
            "unsupported",
            "Approval was recorded, but no supported WARDEN or external policy executor is declared.",
        )
        saved = app.state.warden_workflows.update_defense_candidate(
            request_id,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
            lifecycle.model_dump(mode="json"),
        )
        raise HTTPException(status_code=501, detail={
            "error": "WARDEN_DEFENSE_EXECUTOR_UNSUPPORTED",
            "defense_status": saved["lifecycle"]["status"],
            "message": "The proposal and approval were recorded. No defense action was submitted or executed.",
        })

    @app.post("/v1/defense-policies/{request_id}/execute")
    def execute_public_defense_policy(
        request_id: str,
        request: WardenDefenseExecuteRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        return execute_warden_defense_proposal(request_id, request, x_approval_token)

    @app.get("/v1/kernel/workflows/{request_id}/events")
    def get_kernel_workflow_events(request_id: str) -> dict:
        """Return append-only state transition history for an upper-layer workflow."""
        entry = app.state.warden_workflows.get(request_id)
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})
        return {"request_id": request_id, "items": app.state.warden_workflows.events(request_id)}

    @app.get("/v1/scenarios")
    def list_scenarios() -> dict:
        return {"items": [s.model_dump(mode="json") for s in app.state.engine.scenarios.list_scenarios()]}

    @app.get("/v1/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict:
        try:
            scenario = app.state.engine.scenarios.get_scenario(scenario_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="scenario_id not found") from exc
        return scenario.model_dump(mode="json")

    @app.post("/v1/runs")
    def create_run(
        request: ExperimentRequest,
        background: bool = Query(default=False),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict:
        if idempotency_key:
            with app.state.lock:
                prior_job_id = app.state.idempotency.get(idempotency_key)
                prior_job = dict(app.state.jobs.get(prior_job_id, {})) if prior_job_id else None
                existing = app.state.reports.get(prior_job_id) if prior_job_id else None
            if prior_job:
                return prior_job
            if existing:
                return existing.model_dump(mode="json")
        if background:
            job_id = f"job-{secrets.token_hex(8)}"
            event = Event()
            job = {
                "job_id": job_id,
                "run_id": job_id,
                "experiment_id": request.experiment_id,
                "scenario_id": request.scenario_id,
                "status": "queued",
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            }
            with app.state.lock:
                app.state.jobs[job_id] = job
                app.state.cancel_events[job_id] = event
                if idempotency_key:
                    app.state.idempotency[idempotency_key] = job_id
            future = app.state.executor.submit(_run_job, job_id, request, event)
            with app.state.lock:
                app.state.futures[job_id] = future
            return dict(job)
        try:
            report = run_engine(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="scenario_id not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "INVALID_RUN_REQUEST", "message": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": "RUN_FAILED", "message": type(exc).__name__}) from exc
        store_report(report)
        if idempotency_key:
            with app.state.lock:
                app.state.idempotency[idempotency_key] = report.run_id
        return report.model_dump(mode="json")

    @app.get("/v1/runs")
    def list_runs(limit: int = Query(default=50, ge=1, le=500)) -> dict:
        with app.state.lock:
            reports = list({report.run_id: report for report in app.state.reports.values()}.values())
            jobs = [dict(value) for value in app.state.jobs.values()]
        reports.sort(key=lambda item: item.run_id, reverse=True)
        jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return {
            "items": [
                {"run_id": r.run_id, "experiment_id": r.experiment_id, "scenario_id": r.scenario_id,
                 "status": r.status, "backend_mode": r.backend_mode, "created_at": r.timeline[0].timestamp.isoformat() if r.timeline else None}
                for r in reports[:limit]
            ],
            "jobs": jobs[:limit],
        }

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        # A background job ID resolves to its lifecycle state; result_run_id resolves to the report.
        job = resolve_job_status(run_id)
        if job is not None:
            return job
        with app.state.lock:
            report = app.state.reports.get(run_id)
        if report is not None:
            return report.model_dump(mode="json")
        raise HTTPException(status_code=404, detail="run_id not found")

    @app.post("/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict:
        with app.state.lock:
            job = app.state.jobs.get(run_id)
            future = app.state.futures.get(run_id)
            cancel_event = app.state.cancel_events.get(run_id)
        if job is None or cancel_event is None:
            raise HTTPException(status_code=404, detail="background job not found")
        if job.get("status") in {"completed", "partial", "cancelled", "failed"}:
            return {"job_id": run_id, "status": job.get("status"), "cancelled": job.get("status") == "cancelled"}
        cancel_event.set()
        if future is not None and future.cancel():
            with app.state.lock:
                app.state.jobs[run_id].update(status="cancelled", updated_at=_utcnow())
        else:
            with app.state.lock:
                app.state.jobs[run_id].update(status="cancel_requested", updated_at=_utcnow())
        return {"job_id": run_id, "status": resolve_job_status(run_id)["status"], "cancel_requested": True,
                "note": "Cancellation is cooperative; a synchronous backend call already in flight cannot be forcibly interrupted."}

    @app.get("/v1/runs/{run_id}/observations")
    def get_observations(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return {"run_id": report.run_id, "items": [o.model_dump(mode="json") for o in report.observations]}

    @app.get("/v1/runs/{run_id}/graph")
    def get_graph(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        if report.graph_analysis is None:
            return {"run_id": report.run_id, "graph": None, "note": "Graph analysis is unavailable for this partial run."}
        return {"run_id": report.run_id, "graph": report.graph_analysis.model_dump(mode="json")}

    @app.get("/v1/runs/{run_id}/hypotheses")
    def get_hypotheses(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return {"run_id": report.run_id, "items": [item.model_dump(mode="json") for item in report.hypotheses]}

    @app.get("/v1/runs/{run_id}/investigation")
    def get_investigation(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return {
            "run_id": report.run_id,
            "hypotheses": [item.model_dump(mode="json") for item in report.hypotheses],
            "actions": [item.model_dump(mode="json") for item in report.investigation_actions],
            "experiment_designs": [item.model_dump(mode="json") for item in report.experiment_designs],
        }

    @app.get("/v1/runs/{run_id}/timeline")
    def get_timeline(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return {"run_id": report.run_id, "items": [item.model_dump(mode="json") for item in report.timeline]}

    @app.get("/v1/runs/{run_id}/findings")
    def get_findings(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return {"run_id": report.run_id, "items": [f.model_dump(mode="json") for f in report.findings]}

    @app.get("/v1/runs/{run_id}/evidence")
    def get_run_evidence(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        artifact_id = app.state.run_artifacts.get(report.run_id)
        return {
            "run_id": report.run_id,
            "items": [item.model_dump(mode="json") for item in app.state.evidence_gateway.for_report(
                report,
                report_artifact_id=artifact_id,
                artifacts=app.state.artifacts,
            )],
            "report_artifact_id": artifact_id,
        }

    @app.get("/v1/runs/{run_id}/report")
    def get_run_report(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return report.model_dump(mode="json")

    @app.get("/v1/reports/{run_id}")
    def get_public_report(run_id: str) -> dict:
        return get_run_report(run_id)

    def _run_intervention_locked(
        run_id: str,
        request: InterventionRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        report = get_report_or_404(run_id)
        if report.policy_profile == "read_only":
            raise HTTPException(status_code=403, detail={"error": "READ_ONLY_PROFILE", "message": "This run's policy profile forbids interventions."})
        if request.experiment_id != report.experiment_id:
            raise HTTPException(status_code=400, detail="experiment_id does not match run")
        prior_result = next((item for item in report.intervention_history if item.intervention_id == request.intervention_id), None)
        if prior_result is not None:
            return prior_result.model_dump(mode="json")
        finding = next((item for item in report.findings if item.target_ref == request.target_ref and item.target_type == request.target_type), None)
        if finding is None:
            raise HTTPException(status_code=400, detail="target_ref must match an observed finding in this run")
        if request.authorization_ref is None:
            raise HTTPException(status_code=400, detail="authorization_ref is required for API intervention requests")
        scenario = app.state.engine.scenarios.get_scenario(report.scenario_id)
        capabilities = app.state.engine.runtime.capabilities()
        approval_granted = approval_is_valid(x_approval_token)
        if capabilities.backend_mode == "real" and not approval_granted:
            raise HTTPException(status_code=403, detail={"error": "APPROVAL_REQUIRED", "message": "Configure AGENTGUARD_APPROVAL_TOKEN and provide X-Approval-Token."})
        used = report.budget_usage.intervention_attempts if report.budget_usage else 0
        budget = report.budget_usage.max_interventions if report.budget_usage else 0
        decision = app.state.policy_engine.evaluate(
            scenario, request, capabilities,
            max_interventions=budget,
            interventions_already_attempted=used,
            approval_granted=approval_granted,
        )
        if not decision.allowed:
            code = 501 if decision.status == "unsupported" else 403
            raise HTTPException(status_code=code, detail={"error": decision.error_code, "message": decision.reason})
        result = app.state.engine.runtime.intervene(request)
        report.selected_intervention = request
        report.intervention_result = result
        report.intervention_requests.append(request)
        report.intervention_history.append(result)
        if report.budget_usage is not None:
            report.budget_usage.intervention_attempts += 1
            report.budget_usage.intervention_requests += 1
        if result.effect_status == "simulated":
            report.causal_claim_status = "simulated_only"
        elif result.effect_status == "measured":
            report.causal_claim_status = "causal_validation_required"
        report.evidence.append(EvidenceRecord(
            kind="intervention", grade="synthetic" if report.backend_mode == "synthetic" else "interventional",
            source="agentguard.api.intervention", summary=result.message,
            artifact_refs=result.evidence_refs, run_id=report.run_id, experiment_id=report.experiment_id,
            scenario_id=report.scenario_id, metadata={"status": result.status, "effect_status": result.effect_status},
        ))
        report.timeline.append(RunTimelineEvent(
            sequence=len(report.timeline), stage="intervened",
            status="completed" if result.status == "completed" else "rejected",
            message="API intervention request processed; effectiveness is not inferred.",
            details={"intervention_id": result.intervention_id, "effect_status": result.effect_status, "status": result.status},
        ))
        report.limitations.append("An intervention submitted after the original run was not automatically paired with a fresh baseline/control replay.")
        store_report(report)
        return result.model_dump(mode="json")

    @app.post("/v1/runs/{run_id}/interventions")
    def run_intervention(
        run_id: str,
        request: InterventionRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        with get_run_lock(run_id):
            return _run_intervention_locked(run_id, request, x_approval_token)

    @app.get("/v1/runs/{run_id}/defenses/preview")
    def get_defense_preview(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        preview = report.defense_preview or app.state.defense_service.preview(report)
        lifecycle_by_id = {item.candidate_id: item for item in report.defense_lifecycle}
        for candidate in preview.candidates:
            if candidate.candidate_id in lifecycle_by_id:
                candidate.status = lifecycle_by_id[candidate.candidate_id].status
        return preview.model_dump(mode="json")

    def _transition_defense(
        report: RunReport,
        candidate,
        status: str,
        reason: str,
    ) -> DefenseLifecycleRecord:
        existing = next((item for item in report.defense_lifecycle if item.candidate_id == candidate.candidate_id), None)
        if existing is None:
            existing = app.state.defense_service.initial_lifecycle(candidate)
        try:
            updated = app.state.defense_service.transition(existing, status, reason)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={
                "error": "INVALID_DEFENSE_STATE_TRANSITION",
                "message": str(exc),
                "current_status": existing.status,
                "requested_status": status,
            }) from exc
        if existing in report.defense_lifecycle:
            report.defense_lifecycle[report.defense_lifecycle.index(existing)] = updated
        else:
            report.defense_lifecycle.append(updated)
        candidate.status = updated.status
        return updated

    def _verified_defense_experiences(report: RunReport) -> list[ExperienceRecord]:
        records: dict[str, ExperienceRecord] = {}
        for finding in report.findings:
            for item in app.state.experiences.recommendations(
                scenario_id=report.scenario_id,
                risk_type=finding.risk_type,
                limit=500,
            ):
                records[item.experience_id] = item
        return list(records.values())

    def _preview_defenses_locked(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        preview = app.state.defense_service.preview(
            report,
            experiences=_verified_defense_experiences(report),
        )
        current_states = {item.candidate_id: item for item in report.defense_lifecycle}
        for candidate in preview.candidates:
            lifecycle = current_states.get(candidate.candidate_id)
            if lifecycle is None:
                lifecycle = app.state.defense_service.initial_lifecycle(candidate)
                report.defense_lifecycle.append(lifecycle)
            candidate.status = lifecycle.status
        report.defense_preview = preview
        store_report(report)
        return preview.model_dump(mode="json")

    @app.post("/v1/runs/{run_id}/defenses/preview")
    def preview_defenses(run_id: str) -> dict:
        with get_run_lock(run_id):
            return _preview_defenses_locked(run_id)

    def _execute_defense_locked(
        run_id: str,
        request: DefenseExecuteRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        report = get_report_or_404(run_id)
        if report.policy_profile == "read_only":
            raise HTTPException(status_code=403, detail={"error": "READ_ONLY_PROFILE", "message": "This run's policy profile forbids interventions."})
        preview = report.defense_preview or app.state.defense_service.preview(report)
        candidate = next((item for item in preview.candidates if item.candidate_id == request.candidate_id), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="defense candidate not found in preview")
        manifest = app.state.engine.runtime.capabilities()
        if not request.authorization_ref:
            raise HTTPException(status_code=400, detail="authorization_ref is required")
        intervention_id = request.intervention_id or f"api-{candidate.candidate_id}"
        prior_result = next((item for item in report.intervention_history if item.intervention_id == intervention_id), None)
        if prior_result is not None:
            lifecycle = next((item for item in report.defense_lifecycle if item.candidate_id == candidate.candidate_id), None)
            return {
                "candidate": candidate.model_dump(mode="json"),
                "result": prior_result.model_dump(mode="json"),
                "defense_status": lifecycle.status if lifecycle else "unknown",
                "execution_mode": report.backend_mode,
                "effectiveness_verified": False,
                "idempotent_replay": True,
            }
        if manifest.backend_mode == "real" and not approval_is_valid(x_approval_token):
            _transition_defense(report, candidate, "awaiting_approval", "A real-backend defense requires a valid operator approval token.")
            store_report(report)
            raise HTTPException(status_code=403, detail={
                "error": "APPROVAL_REQUIRED",
                "message": "Real-backend defense requires a configured operator approval token.",
                "defense_status": "awaiting_approval",
            })
        budget = report.budget_usage.max_interventions if report.budget_usage else 0
        used = report.budget_usage.intervention_attempts if report.budget_usage else 0
        intervention = InterventionRequest(
            experiment_id=report.experiment_id,
            intervention_id=intervention_id,
            target_type=candidate.target_type,
            target_ref=candidate.target_ref,
            operation=candidate.operation,
            control_group="matched_control_v1",
            parameters={"scope": candidate.scope, "approval_required": candidate.approval_required},
            authorization_ref=request.authorization_ref,
        )
        scenario = app.state.engine.scenarios.get_scenario(report.scenario_id)
        decision = app.state.policy_engine.evaluate(
            scenario, intervention, manifest, max_interventions=budget,
            interventions_already_attempted=used,
            approval_granted=approval_is_valid(x_approval_token),
        )
        if not decision.allowed:
            if decision.status == "approval_required":
                target_status = "awaiting_approval"
            elif decision.status == "unsupported":
                target_status = "unsupported"
            else:
                target_status = "rejected"
            _transition_defense(report, candidate, target_status, decision.reason)
            store_report(report)
            code = 501 if decision.status == "unsupported" else 403
            raise HTTPException(status_code=code, detail={
                "error": decision.error_code,
                "message": decision.reason,
                "defense_status": target_status,
            })
        if candidate.approval_required:
            _transition_defense(report, candidate, "approved", "The configured operator approval gate and policy checks passed.")
        _transition_defense(report, candidate, "submitted", "The candidate passed policy checks and was submitted to the declared runtime.")
        try:
            result = app.state.engine.runtime.intervene(intervention)
        except Exception:
            _transition_defense(report, candidate, "failed", "The declared runtime raised an exception during defense execution.")
            store_report(report)
            raise
        report.selected_intervention = intervention
        report.intervention_result = result
        report.intervention_requests.append(intervention)
        report.intervention_history.append(result)
        if report.budget_usage is not None:
            report.budget_usage.intervention_attempts += 1
            report.budget_usage.intervention_requests += 1
        if result.effect_status == "simulated":
            report.causal_claim_status = "simulated_only"
        elif result.effect_status == "measured":
            report.causal_claim_status = "causal_validation_required"
        report.evidence.append(EvidenceRecord(
            kind="intervention", grade="synthetic" if report.backend_mode == "synthetic" else "interventional",
            source="agentguard.api.defense", summary=result.message,
            artifact_refs=result.evidence_refs, run_id=report.run_id, experiment_id=report.experiment_id,
            scenario_id=report.scenario_id, metadata={"candidate_id": candidate.candidate_id,
            "status": result.status, "effect_status": result.effect_status,
            "risk_type": candidate.risk_type},
        ))
        if result.status == "completed":
            _transition_defense(report, candidate, "executed", "Runtime returned a completed intervention result; effectiveness remains unverified.")
            _transition_defense(report, candidate, "verification_pending", "Independent matched-run, benign-utility and provenance checks have not been completed.")
        elif result.status == "unsupported":
            _transition_defense(report, candidate, "unsupported", result.message or "Runtime does not support this defense operation.")
        elif result.status == "rejected":
            _transition_defense(report, candidate, "rejected", result.message or "Runtime rejected the defense operation.")
        else:
            _transition_defense(report, candidate, "failed", result.message or "Runtime defense operation failed.")
        report.timeline.append(RunTimelineEvent(
            sequence=len(report.timeline), stage="intervened",
            status="completed" if result.status == "completed" else "failed" if result.status == "runtime_error" else "rejected",
            message="Defense operation was recorded; completion does not imply effectiveness verification.",
            details={"candidate_id": candidate.candidate_id, "status": result.status, "effect_status": result.effect_status},
        ))
        report.limitations.append("Defense execution does not itself establish risk reduction; run matched treatment/control and normal-utility checks.")
        store_report(report)
        lifecycle = next(item for item in report.defense_lifecycle if item.candidate_id == candidate.candidate_id)
        return {
            "candidate": candidate.model_dump(mode="json"),
            "policy_decision": decision.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "defense_status": lifecycle.status,
            "execution_mode": report.backend_mode,
            "effectiveness_verified": False,
        }

    @app.post("/v1/runs/{run_id}/defenses/execute")
    def execute_defense(
        run_id: str,
        request: DefenseExecuteRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        with get_run_lock(run_id):
            return _execute_defense_locked(run_id, request, x_approval_token)

    def _verify_defense_locked(run_id: str, request: DefenseVerifyRequest) -> dict:
        report = get_report_or_404(run_id)
        preview = report.defense_preview or app.state.defense_service.preview(report)
        candidate = next((item for item in preview.candidates if item.candidate_id == request.candidate_id), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="defense candidate not found in preview")
        try:
            verification = app.state.defense_service.verify_paired(
                report, candidate, request.baseline_risk, request.post_defense_risk,
                request.baseline_utility, request.post_defense_utility, seed=request.seed,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        lifecycle = next((item for item in report.defense_lifecycle if item.candidate_id == candidate.candidate_id), None)
        if lifecycle is None or lifecycle.status not in {"executed", "verification_pending"}:
            raise HTTPException(status_code=409, detail={
                "error": "DEFENSE_NOT_EXECUTED",
                "message": "A defense candidate must be executed before its outcome can be evaluated.",
                "defense_status": lifecycle.status if lifecycle else "proposed",
            })
        arrays = {
            "baseline_risk": request.baseline_risk,
            "post_defense_risk": request.post_defense_risk,
            "baseline_utility": request.baseline_utility,
            "post_defense_utility": request.post_defense_utility,
        }
        verified_refs = app.state.evidence_gateway.verify_defense_artifacts(
            request.source_artifact_refs,
            run_id=report.run_id,
            candidate_id=candidate.candidate_id,
            artifacts=app.state.artifacts,
            arrays=arrays,
        )
        verification.source_artifact_refs = verified_refs
        verification.provenance_verified = bool(verified_refs) and report.backend_mode == "real"
        verification.verification_status = "verified" if verification.provenance_verified else "unverified"
        verification.evidence_mode = "measured" if verification.provenance_verified else "synthetic" if report.backend_mode == "synthetic" else "observed"
        verification.risk_transfer_checked = verification.provenance_verified
        verification.rollback_tested = verification.provenance_verified
        if not verification.provenance_verified:
            verification.limitations.append("Caller-supplied metric arrays are statistical input only; no matching hash-checked AgentGuard artifact proved their provenance and controls.")
        if report.backend_mode == "synthetic":
            verification.limitations.append("Synthetic execution cannot establish real defense effectiveness.")
        if verification.provenance_verified and verification.status == "passed":
            lifecycle = app.state.defense_service.transition(lifecycle, "verified", "Paired result passed and matching real-run artifacts verified provenance, controls, utility, and rollback evidence.")
        elif verification.provenance_verified and verification.status == "failed":
            lifecycle = app.state.defense_service.transition(lifecycle, "failed", "Hash-checked paired evidence did not meet defense outcome criteria.")
        report.defense_lifecycle[report.defense_lifecycle.index(next(item for item in report.defense_lifecycle if item.candidate_id == candidate.candidate_id))] = lifecycle
        candidate.status = lifecycle.status
        report.defense_verification = verification
        report.evidence.append(EvidenceRecord(
            kind="evaluation",
            grade="synthetic" if report.backend_mode == "synthetic" else "interventional" if verification.provenance_verified else "observational",
            source="agentguard.api.defense_verification",
            summary=f"Paired defense summary status: {verification.status}; provenance status: {verification.verification_status}.",
            artifact_refs=verified_refs,
            run_id=report.run_id,
            experiment_id=report.experiment_id,
            scenario_id=report.scenario_id,
            metadata={"candidate_id": candidate.candidate_id, "status": verification.status,
                      "risk_type": candidate.risk_type, "methodology_reviewed": verification.provenance_verified},
        ))
        report.limitations.append("A statistical result is not a verified defense outcome unless the lifecycle reaches verified with provenance-backed controls and utility evidence.")
        store_report(report)
        return {**verification.model_dump(mode="json"), "defense_status": lifecycle.status}

    @app.post("/v1/runs/{run_id}/defenses/verify")
    def verify_defense(run_id: str, request: DefenseVerifyRequest) -> dict:
        with get_run_lock(run_id):
            return _verify_defense_locked(run_id, request)

    @app.get("/v1/runs/{run_id}/defenses/verification")
    def get_defense_verification(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        if report.defense_verification is None:
            raise HTTPException(status_code=404, detail="defense verification not found")
        return report.defense_verification.model_dump(mode="json")

    @app.get("/v1/runs/{run_id}/experiments/paired")
    def list_paired_experiments(run_id: str) -> dict:
        report = get_report_or_404(run_id)
        return {"run_id": report.run_id,
                "items": [item.model_dump(mode="json") for item in report.paired_experiments]}

    @app.post("/v1/runs/{run_id}/experiments/paired")
    def execute_paired_experiment(
        run_id: str,
        request: PairedExperimentRunRequest,
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> dict:
        with get_run_lock(run_id):
            report = get_report_or_404(run_id)
            if report.policy_profile == "read_only":
                raise HTTPException(status_code=403, detail={"error": "READ_ONLY_PROFILE", "message": "This run's policy profile forbids intervention experiments."})
            if report.request_snapshot is None:
                raise HTTPException(status_code=409, detail="run report does not contain a reproducible request snapshot")
            design = next((item for item in report.experiment_designs if request.design_id is None or item.design_id == request.design_id), None)
            if design is None:
                raise HTTPException(status_code=404, detail="paired experiment design not found")
            existing_execution = next((item for item in report.paired_experiments if item.design_id == design.design_id), None)
            if existing_execution is not None:
                return existing_execution.model_dump(mode="json")
            action = next((item for item in report.investigation_actions
                           if item.hypothesis_id == design.hypothesis_id and
                           (request.action_id is None or item.action_id == request.action_id)), None)
            if action is None:
                raise HTTPException(status_code=404, detail="matching investigation action not found")
            if action.operation != design.treatment_operation:
                raise HTTPException(status_code=400, detail="selected action operation does not match the stored design")
            capabilities = app.state.engine.runtime.capabilities()
            if capabilities.backend_mode == "real" and app.state.paired_runner.paired_executor is None:
                raise HTTPException(status_code=501, detail={"error": "PAIRED_REPLAY_UNAVAILABLE", "message": "Real paired replay requires an explicit validated paired_executor; a file-level run API is not enough."})
            if capabilities.backend_mode == "real" and not approval_is_valid(x_approval_token):
                raise HTTPException(status_code=403, detail={"error": "APPROVAL_REQUIRED", "message": "Real-backend paired interventions require operator approval."})
            if not request.authorization_ref:
                raise HTTPException(status_code=400, detail="authorization_ref is required for paired intervention experiments")
            budget = report.budget_usage.max_interventions if report.budget_usage else 0
            used = report.budget_usage.intervention_attempts if report.budget_usage else 0
            repetitions = design.repetitions
            if used + repetitions > budget:
                raise HTTPException(status_code=403, detail={
                    "error": "BUDGET_EXCEEDED",
                    "message": f"Paired design requires {repetitions} treatment attempts but only {max(0, budget-used)} remain. Re-run with a larger max_interventions budget.",
                })
            scenario = app.state.engine.scenarios.get_scenario(report.scenario_id)
            scope = "synthetic_demo" if capabilities.backend_mode == "synthetic" else "isolated_test_environment"
            template = InterventionRequest(
                experiment_id=report.experiment_id,
                intervention_id=f"paired-template-{design.design_id}",
                target_type=action.target_type,
                target_ref=action.target_ref,
                operation=action.operation,
                control_group="matched_control_and_treatment",
                parameters={"scope": scope, "approval_required": capabilities.backend_mode == "real"},
                authorization_ref=request.authorization_ref,
            )
            decision = app.state.policy_engine.evaluate(
                scenario, template, capabilities, max_interventions=budget,
                interventions_already_attempted=used,
                approval_granted=approval_is_valid(x_approval_token),
            )
            if not decision.allowed:
                code = 501 if decision.status == "unsupported" else 403
                raise HTTPException(status_code=code, detail={"error": decision.error_code, "message": decision.reason})
            # Reserve the full declared treatment budget before invoking the runner. This prevents
            # retries or partial failures from silently freeing already-authorized write capacity.
            if report.budget_usage is not None:
                report.budget_usage.intervention_attempts += repetitions
                report.budget_usage.intervention_requests += repetitions
            try:
                execution = app.state.paired_runner.run(
                    run_id=report.run_id, scenario=scenario, base_request=report.request_snapshot,
                    design=design, intervention_template=template,
                    approval_granted=approval_is_valid(x_approval_token),
                )
            except PairedExecutionUnsupported as exc:
                report.limitations.append("Paired executor was unavailable after budget reservation; the reserved intervention budget remains consumed for safety.")
                store_report(report)
                raise HTTPException(status_code=501, detail={"error": "PAIRED_REPLAY_UNAVAILABLE", "message": str(exc)}) from exc
            except ValueError as exc:
                report.limitations.append("Paired execution failed after budget reservation; the reserved intervention budget remains consumed for safety.")
                report.timeline.append(RunTimelineEvent(
                    sequence=len(report.timeline), stage="verification", status="failed",
                    message="Paired experiment runner rejected or failed the design.",
                    details={"design_id": design.design_id, "error_code": type(exc).__name__},
                ))
                store_report(report)
                raise HTTPException(status_code=400, detail={"error": "PAIRED_DESIGN_INVALID", "message": str(exc)}) from exc
            except Exception as exc:
                report.limitations.append("Paired execution raised after budget reservation; the reserved intervention budget remains consumed for safety.")
                report.timeline.append(RunTimelineEvent(
                    sequence=len(report.timeline), stage="verification", status="failed",
                    message="Paired experiment execution failed; raw exception text was not stored.",
                    details={"design_id": design.design_id, "error_code": type(exc).__name__},
                ))
                store_report(report)
                raise HTTPException(status_code=502, detail={"error": "PAIRED_EXECUTION_FAILED", "message": "paired experiment failed; inspect the saved run report for its failure code"}) from exc
            artifact = app.state.artifacts.put(
                execution.model_dump_json(indent=2).encode("utf-8"),
                name=f"paired-experiment-{execution.design_id}.json",
                description="Paired control/treatment/negative-control execution report",
                tags=["paired-experiment", execution.evidence_mode, execution.status],
                metadata={"run_id": report.run_id, "experiment_id": report.experiment_id,
                          "design_id": execution.design_id, "completed_pairs": execution.completed_pairs},
                media_type="application/json",
            )
            execution.artifact_id = artifact.artifact_ref.artifact_id
            report.paired_experiments.append(execution)
            for pair in execution.pairs:
                if pair.intervention_request is not None:
                    report.intervention_requests.append(pair.intervention_request)
                if pair.intervention_result is not None:
                    report.intervention_history.append(pair.intervention_result)
                    report.intervention_result = pair.intervention_result
                    report.selected_intervention = pair.intervention_request
            if report.budget_usage is not None:
                report.budget_usage.intervention_attempts = min(
                    report.budget_usage.intervention_attempts, report.budget_usage.max_interventions
                )
            report.causal_claim_status = "simulated_only" if execution.evidence_mode == "synthetic" else "causal_validation_required"
            report.evidence.append(EvidenceRecord(
                kind="evaluation", grade="synthetic" if execution.evidence_mode == "synthetic" else "observational",
                source="agentguard.paired_runner", summary=(f"Paired design {execution.design_id} completed {execution.completed_pairs}/{execution.requested_pairs} pairs; "
                "aggregate statistics are descriptive and not an automatic causal validation."),
                artifact_refs=[artifact.artifact_ref.artifact_id], run_id=report.run_id,
                experiment_id=report.experiment_id, scenario_id=report.scenario_id,
                source_version="paired-runner-0.4", metadata={"design_id": execution.design_id,
                "status": execution.status, "evidence_mode": execution.evidence_mode},
            ))
            report.timeline.append(RunTimelineEvent(
                sequence=len(report.timeline), stage="verification",
                status="completed" if execution.status == "completed" else "partial",
                message="Paired experiment execution report assembled.",
                details={"design_id": execution.design_id, "completed_pairs": execution.completed_pairs,
                         "requested_pairs": execution.requested_pairs, "artifact_id": artifact.artifact_ref.artifact_id},
            ))
            report.limitations.extend(execution.limitations)
            store_report(report)
            return execution.model_dump(mode="json")

    @app.get("/v1/artifacts")
    def list_artifacts(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        items = app.state.artifacts.list_metadata()
        return {"items": [item.model_dump(mode="json") for item in items[-limit:]], "total": len(items)}

    @app.get("/v1/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> dict:
        try:
            return app.state.artifacts.get_metadata(artifact_id).model_dump(mode="json")
        except (ArtifactNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc

    @app.get("/v1/artifacts/{artifact_id}/content")
    def get_artifact_content(artifact_id: str) -> Response:
        try:
            metadata = app.state.artifacts.get_metadata(artifact_id)
            data = app.state.artifacts.get_bytes(artifact_id)
        except (ArtifactNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return Response(content=data, media_type=metadata.artifact_ref.media_type,
                        headers={"Content-Disposition": f'attachment; filename="{artifact_id}.bin"'})

    @app.post(
        "/v1/probes/train",
        response_model=BinaryProbeTrainingReport,
        deprecated=True,
        description=(
            "Deprecated AgentGuard compatibility baseline for caller-supplied vectors only. "
            "It does not extract model activations or call WARDEN. WARDEN owns production "
            "Probe resources and causal audits; its current public API has no equivalent "
            "Probe training endpoint."
        ),
    )
    def train_binary_probe(request: BinaryProbeTrainRequest) -> BinaryProbeTrainingReport:
        try:
            train_idx, validation_idx, test_idx = split_by_group_three_way(
                request.labels, request.case_families,
                test_fraction=request.test_fraction,
                validation_fraction=request.validation_fraction, seed=request.seed,
            )
            y_train = [request.labels[i] for i in train_idx]
            y_validation = [request.labels[i] for i in validation_idx]
            y_test = [request.labels[i] for i in test_idx]
            if any(set(labels) != {0, 1} for labels in (y_train, y_validation, y_test)):
                raise ValueError("group-isolated split did not retain both classes in train, validation and test; provide more balanced case families")
            train_groups = {request.case_families[i] for i in train_idx}
            validation_groups = {request.case_families[i] for i in validation_idx}
            test_groups = {request.case_families[i] for i in test_idx}
            if not train_groups.isdisjoint(validation_groups) or not train_groups.isdisjoint(test_groups) or not validation_groups.isdisjoint(test_groups):
                raise RuntimeError("case-family leakage detected by split verifier")
            model = BinaryLinearProbe(
                learning_rate=request.learning_rate, epochs=request.epochs,
                l2=request.l2, threshold=request.threshold if request.threshold is not None else 0.5,
            ).fit([request.features[i] for i in train_idx], y_train, request.feature_names)
            validation_scores = model.predict_scores([request.features[i] for i in validation_idx])
            if request.threshold is None:
                model.threshold = select_f1_threshold(y_validation, validation_scores)
            validation_metrics = evaluate_binary_classifier(y_validation, validation_scores, threshold=model.threshold)
            scores = model.predict_scores([request.features[i] for i in test_idx])
            metrics = evaluate_binary_classifier(y_test, scores, threshold=model.threshold)
            dataset_payload = json.dumps({"features": request.features, "labels": request.labels,
                                          "case_families": request.case_families}, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")
            dataset_hash = hashlib.sha256(dataset_payload).hexdigest()
            model_payload = model.to_dict()
            model_payload["dataset_sha256"] = dataset_hash
            model_payload["seed"] = request.seed
            model_payload["split_strategy"] = "case_family_group_holdout"
            model_payload["dataset_name"] = request.dataset_name
            model_payload["dataset_version"] = request.dataset_version
            model_payload["label_source"] = request.label_source
            model_payload["representation_source"] = request.representation_source
            artifact = app.state.artifacts.put(
                json.dumps(model_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                name="binary-linear-probe.json",
                description="Binary logistic linear probe trained on caller-supplied numeric representations",
                tags=["linear-probe", "supervised-detector"],
                metadata={"dataset_sha256": dataset_hash, "dataset_name": request.dataset_name,
                          "dataset_version": request.dataset_version, "label_source": request.label_source,
                          "representation_source": request.representation_source, "seed": request.seed,
                          "train_samples": len(train_idx), "validation_samples": len(validation_idx),
                          "test_samples": len(test_idx), "train_group_count": len(train_groups),
                          "validation_group_count": len(validation_groups), "test_group_count": len(test_groups),
                          "validation_f1": validation_metrics["f1"], "test_f1": metrics["f1"],
                          "split_strategy": "case_family_group_holdout", "f1": metrics["f1"]},
                media_type="application/json",
            )
            return BinaryProbeTrainingReport(
                model_artifact_id=artifact.artifact_ref.artifact_id,
                dataset_sha256=dataset_hash, dataset_name=request.dataset_name,
                dataset_version=request.dataset_version, label_source=request.label_source,
                representation_source=request.representation_source, seed=request.seed,
                train_samples=len(train_idx), validation_samples=len(validation_idx), test_samples=len(test_idx),
                train_group_count=len(train_groups), validation_group_count=len(validation_groups),
                test_group_count=len(test_groups), threshold=model.threshold,
                validation_metrics=validation_metrics, group_split_verified=True, metrics=metrics,
                notes=[
                    "The probe was trained only on the supplied numeric vectors and labels; this API does not extract model activations.",
                    "Train/validation/test isolation is by case_families; verify that family identifiers encode the intended independence unit.",
                    "If threshold is omitted, it is selected on validation F1 and evaluated once on the held-out test split.",
                    "Scores and metrics do not establish a causal mechanism or generalize beyond the evaluated data.",
                    "The source dataset is not stored as an artifact by this endpoint; only a hash and trained model are retained.",
                ],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "PROBE_TRAINING_INVALID", "message": str(exc)}) from exc

    @app.post(
        "/v1/probes/predict",
        response_model=BinaryProbePredictionReport,
        deprecated=True,
        description=(
            "Deprecated AgentGuard compatibility prediction for caller-supplied vectors only. "
            "It does not extract model activations or call WARDEN. WARDEN owns production "
            "Probe resources and causal audits; its current public API has no equivalent "
            "Probe prediction endpoint."
        ),
    )
    def predict_with_probe(request: BinaryProbePredictRequest) -> BinaryProbePredictionReport:
        try:
            metadata = app.state.artifacts.get_metadata(request.model_artifact_id)
            if "linear-probe" not in metadata.tags:
                raise HTTPException(status_code=400, detail="artifact is not a registered linear-probe model")
            payload = json.loads(app.state.artifacts.get_bytes(request.model_artifact_id).decode("utf-8"))
            model = BinaryLinearProbe.from_dict(payload)
            scores = model.predict_scores(request.features)
            predictions = model.predict(request.features)
        except ArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail="probe model artifact not found") from exc
        except HTTPException:
            raise
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail={"error": "PROBE_PREDICTION_INVALID", "message": str(exc)}) from exc
        return BinaryProbePredictionReport(
            model_artifact_id=request.model_artifact_id,
            scores=scores,
            predictions=predictions,
            threshold=model.threshold,
            evidence_mode="observed",
            limitations=[
                "Predictions are model outputs for caller-supplied vectors, not ground-truth labels.",
                "This endpoint does not extract activations from a model or establish a causal mechanism.",
            ],
        )

    @app.post("/v1/experiences", response_model=ExperienceRecord)
    def add_experience(record: ExperienceRecord) -> ExperienceRecord:
        if record.verification_status == "verified":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "VERIFIED_EXPERIENCE_REQUIRES_TRUSTED_REVIEW",
                    "message": "Public submissions cannot self-assert verified reward evidence.",
                },
            )
        try:
            return app.state.experiences.add(record)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(status_code=409, detail="experience_id already exists") from exc
            raise HTTPException(status_code=400, detail={"error": "INVALID_EXPERIENCE", "message": type(exc).__name__}) from exc

    @app.get("/v1/experiences", response_model=ExperienceSearchResult)
    def search_experiences(
        q: str = "", scenario_id: str | None = None, risk_type: str | None = None,
        action_type: str | None = None, model_version: str | None = None,
        policy_version: str | None = None, evidence_grade: str | None = None,
        verified_after: datetime | None = None, dataset_partition: str | None = None,
        verified_only: bool = False, limit: int = Query(default=50, ge=1, le=500),
    ) -> ExperienceSearchResult:
        try:
            items = app.state.experiences.search(
                q,
                scenario_id=scenario_id,
                risk_type=risk_type,
                action_type=action_type,
                model_version=model_version,
                policy_version=policy_version,
                evidence_grade=evidence_grade,
                verified_after=verified_after,
                dataset_partition=dataset_partition,
                verified_only=verified_only,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "INVALID_EXPERIENCE_FILTER"}) from exc
        return ExperienceSearchResult(query=q, items=items, total=len(items))

    @app.get("/v1/experiences/recommendations")
    def experience_recommendations(
        scenario_id: str | None = None,
        risk_type: str | None = None,
        action_type: str | None = None,
        case_family: str | None = None,
        model_version: str | None = None,
        policy_version: str | None = None,
        evidence_grade: str | None = None,
        verified_after: datetime | None = None,
        limit: int = Query(default=5, ge=1, le=50),
    ) -> dict:
        items = app.state.experiences.recommendations(
            scenario_id=scenario_id,
            risk_type=risk_type,
            action_type=action_type,
            case_family=case_family,
            model_version=model_version,
            policy_version=policy_version,
            evidence_grade=evidence_grade,
            verified_after=verified_after,
            limit=limit,
        )
        return {"items": [item.model_dump(mode="json") for item in items],
                "note": "Only complete, non-conflicting verified training records are returned; same-family, test, held-out, expired, and unverified records are excluded. Recommendations do not execute actions."}

    @app.get("/v1/experiences/recommendation-policies")
    def list_experience_recommendation_policies() -> dict:
        active = app.state.experiences.active_policy()
        return {
            "active": active.model_dump(mode="json"),
            "versions": [item.model_dump(mode="json") for item in app.state.experiences.policy_versions()],
            "events": app.state.experiences.policy_history(),
        }

    @app.post("/v1/experiences/recommendation-policies", response_model=ExperienceRecommendationPolicy)
    def save_experience_recommendation_policy(
        policy: ExperienceRecommendationPolicy,
        activate: bool = False,
        x_agentguard_token: str | None = Header(default=None, alias="X-AgentGuard-Token"),
        x_agentguard_operator_token: str | None = Header(default=None, alias="X-AgentGuard-Operator-Token"),
    ) -> ExperienceRecommendationPolicy:
        require_operator_token(x_agentguard_operator_token or x_agentguard_token)
        try:
            return app.state.experiences.save_policy(policy, activate=activate)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": "POLICY_VERSION_IMMUTABLE"}) from exc

    @app.post("/v1/experiences/recommendation-policies/{policy_version}/rollback")
    def rollback_experience_recommendation_policy(
        policy_version: str,
        reason: str = Query(default="operator_rollback", min_length=1, max_length=256),
        x_agentguard_token: str | None = Header(default=None, alias="X-AgentGuard-Token"),
        x_agentguard_operator_token: str | None = Header(default=None, alias="X-AgentGuard-Operator-Token"),
    ) -> dict:
        require_operator_token(x_agentguard_operator_token or x_agentguard_token)
        try:
            app.state.experiences.rollback_policy(policy_version, reason=reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "RECOMMENDATION_POLICY_NOT_FOUND"}) from exc
        return {"active": app.state.experiences.active_policy().model_dump(mode="json")}

    @app.get("/v1/experiences/bandit-recommendations", response_model=ExperienceBanditRecommendationReport)
    def experience_bandit_recommendations(
        scenario_id: str | None = None,
        risk_type: str | None = None,
        case_family: str | None = None,
        target_partition: Literal["training", "validation", "test", "held_out_family"] = "held_out_family",
        model_version: str | None = None,
        record_policy_version: str | None = None,
        recommendation_policy_version: str | None = None,
        seed: int = 7,
        limit: int = Query(default=5, ge=1, le=50),
    ) -> dict:
        records = app.state.experiences.search(
            scenario_id=scenario_id,
            risk_type=risk_type,
            verified_only=False,
            limit=500,
        )
        try:
            policy = app.state.experiences.policy(recommendation_policy_version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": "RECOMMENDATION_POLICY_NOT_FOUND"}) from exc
        return recommend_actions(
            records,
            scenario_id=scenario_id,
            risk_type=risk_type,
            case_family=case_family,
            target_partition=target_partition,
            model_version=model_version,
            policy_version=record_policy_version,
            conflicted_experience_ids=app.state.experiences.conflicted_experience_ids(),
            policy=policy,
            seed=seed,
            limit=limit,
        )

    @app.get("/v1/experiences/action-statistics")
    def experience_action_statistics(verified_only: bool = True) -> dict:
        return {"items": app.state.experiences.action_statistics(verified_only=verified_only)}

    @app.post("/v1/attribution/nodes/analyze", response_model=AttributionNodeAnalysisReport, status_code=201)
    def analyze_attribution_node_endpoint(request: AttributionNodeAnalysisRequest) -> AttributionNodeAnalysisReport:
        try:
            report = analyze_attribution_node(request)
            artifact = app.state.artifacts.put(
                report.model_dump_json(indent=2).encode("utf-8"),
                name=f"attribution-node-{report.analysis_id}.json",
                description="Secret recovery mediation summary for a candidate information-flow node",
                tags=["causal-attribution", report.evidence_mode, request.node_type],
                metadata={"analysis_id": report.analysis_id, "node_id": report.node_id,
                          "evidence_mode": report.evidence_mode, "causal_claim_status": report.causal_claim_status},
                media_type="application/json",
            )
            return report.model_copy(update={"report_artifact_id": artifact.artifact_ref.artifact_id})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "ATTRIBUTION_INPUT_INVALID", "message": str(exc)}) from exc

    @app.post("/v1/attribution/path-shift", response_model=PathShiftAnalysisReport, status_code=201)
    def analyze_path_shift_endpoint(request: PathShiftAnalysisRequest) -> PathShiftAnalysisReport:
        try:
            report = analyze_path_shift(request)
            artifact = app.state.artifacts.put(
                report.model_dump_json(indent=2).encode("utf-8"),
                name=f"path-shift-{report.analysis_id}.json",
                description="Descriptive path contribution distribution comparison",
                tags=["path-shift", report.evidence_mode],
                metadata={"analysis_id": report.analysis_id, "protocol_fingerprint": report.protocol_fingerprint,
                          "evidence_mode": report.evidence_mode},
                media_type="application/json",
            )
            return report.model_copy(update={"report_artifact_id": artifact.artifact_ref.artifact_id})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "PATH_SHIFT_INPUT_INVALID", "message": str(exc)}) from exc

    @app.post("/v1/studies", response_model=RegisteredStudy, status_code=201)
    def register_study(request: StudyRegistrationRequest) -> RegisteredStudy:
        """Create an immutable preregistration before inspecting study outcomes."""
        try:
            return app.state.research_registry.register(request)
        except Exception as exc:
            raise HTTPException(status_code=409, detail={"error": "STUDY_REGISTRATION_FAILED", "code": type(exc).__name__}) from exc

    @app.get("/v1/studies")
    def list_studies(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        items = app.state.research_registry.list(limit=limit)
        return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}

    @app.get("/v1/studies/{study_id}", response_model=RegisteredStudy)
    def get_study(study_id: str) -> RegisteredStudy:
        study = app.state.research_registry.get(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study_id not found")
        return study

    @app.get("/v1/studies/{study_id}/integrity")
    def verify_study_integrity(study_id: str) -> dict:
        study = app.state.research_registry.get(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study_id not found")
        return {"study_id": study_id, "registration_sha256": study.registration_sha256,
                "integrity_valid": app.state.research_registry.verify_registration(study_id),
                "note": "Hash integrity detects accidental changes; it is not a trusted timestamp or external notarization."}

    @app.post("/v1/studies/{study_id}/analyses", response_model=StudyAnalysisReport, status_code=201)
    def analyze_study(study_id: str, request: StudyAnalysisRequest) -> StudyAnalysisReport:
        study = app.state.research_registry.get(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study_id not found")
        try:
            analysis = analyze_registered_study(study, request)
            artifact = app.state.artifacts.put(
                analysis.model_dump_json(indent=2).encode("utf-8"),
                name=f"study-analysis-{analysis.analysis_id}.json",
                description="Preregistered, case-family-aware statistical analysis report",
                tags=["study-analysis", analysis.evidence_mode, analysis.multiplicity_method],
                metadata={"study_id": study_id, "analysis_id": analysis.analysis_id,
                          "registration_sha256": study.registration_sha256,
                          "analysis_sha256": analysis.analysis_sha256},
                media_type="application/json",
            )
            analysis = analysis.model_copy(update={"report_artifact_id": artifact.artifact_ref.artifact_id})
            app.state.research_registry.add_analysis(analysis)
            return analysis
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "STUDY_ANALYSIS_INVALID", "message": str(exc)}) from exc

    @app.get("/v1/studies/{study_id}/analyses")
    def list_study_analyses(study_id: str) -> dict:
        if app.state.research_registry.get(study_id) is None:
            raise HTTPException(status_code=404, detail="study_id not found")
        items = app.state.research_registry.analyses(study_id)
        return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}

    return app


CONSOLE_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentGuard 控制台</title><style>
:root{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#202633;background:#f4f6f9}body{margin:0}header{background:#182334;color:#fff;padding:22px 28px}header h1{margin:0;font-size:23px}header p{margin:7px 0 0;color:#cbd5e1;font-size:13px}.wrap{max-width:1180px;margin:22px auto;padding:0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.card{background:#fff;border:1px solid #e0e5ec;border-radius:12px;padding:18px;box-shadow:0 2px 5px #10182808}.card h2{font-size:16px;margin:0 0 12px}.muted{color:#64748b;font-size:13px}button,select,input{font:inherit;border:1px solid #cbd5e1;border-radius:7px;padding:8px 11px;background:white}button{background:#1d4ed8;color:white;border:0;cursor:pointer}button.secondary{background:#e8edf5;color:#243044}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5e7eb;border-radius:8px;padding:14px;font-size:12px;max-height:440px;overflow:auto}.pill{display:inline-block;padding:3px 8px;background:#e9eef8;border-radius:20px;font-size:12px;margin:2px}.section{margin-top:16px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}#status{margin-left:10px;font-size:13px;color:#475569}@media(max-width:640px){header{padding:18px}.wrap{padding:0 10px}}
</style></head><body><header><h1>AgentGuard</h1><p>实验运行 · 风险发现 · 调查假设 · 防御预览 · 证据审计</p></header>
<div class="wrap"><div class="grid"><section class="card"><h2>启动实验</h2><p class="muted">默认使用合成 Mock 后端；结果不能作为真实模型实验数据。</p><label for="token">API token（仅在服务端启用鉴权时填写）</label><input id="token" type="password" autocomplete="off" placeholder="可留空" style="width:100%;box-sizing:border-box;margin-top:6px;margin-bottom:12px"><label for="scenario">场景</label><br><select id="scenario" style="width:100%;margin-top:6px"></select><div class="actions"><button id="run">运行演示</button><button class="secondary" id="refresh">刷新场景</button><span id="status"></span></div></section>
<section class="card"><h2>后端能力</h2><div id="capabilities" class="muted">加载中…</div></section><section class="card"><h2>最近运行</h2><div id="runs" class="muted">尚无运行记录</div><div class="actions"><button class="secondary" id="loadRuns">刷新运行</button></div></section></div>
<section class="card section"><h2>运行详情</h2><div id="summary" class="muted">选择或启动一次运行后查看结果。</div><div class="actions"><button class="secondary" id="preview">生成防御预览</button><button class="secondary" id="evidence">查看证据索引</button></div><pre id="details">尚无报告</pre></section>
<section class="card section"><h2>交互图与候选路径</h2><p class="muted">仅绘制采集到的事件边；候选路径是调查线索，不代表合谋或因果关系。</p><div id="graph" class="muted">运行一次实验后查看交互图。</div></section>
<p class="muted">本控制台只调用本服务 API，不提供任意 Shell、Python 或文件系统执行入口。真实后端的高影响操作必须在隔离环境中经授权执行。</p></div>
<script>
let currentRun=null;const $=id=>document.getElementById(id);const show=(id,v)=>$(id).textContent=typeof v==='string'?v:JSON.stringify(v,null,2);
async function api(path,opts={}){const token=$('token')?.value||'';const auth=token?{'X-AgentGuard-Token':token}:{};const r=await fetch(path,{...opts,headers:{'Content-Type':'application/json',...auth,...(opts.headers||{})}});const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail?.message||data.detail||r.statusText);return data}
async function scenarios(){const data=await api('/v1/scenarios');$('scenario').replaceChildren(...data.items.map(s=>{const o=document.createElement('option');o.value=s.scenario_id;o.textContent=s.name+' ('+s.scenario_id+')';return o}));}
async function loadCaps(){const c=await api('/v1/capabilities');show('capabilities',{provider:c.provider_id,mode:c.backend_mode,capabilities:c.capabilities,limitations:c.limitations});}
async function loadRuns(){const d=await api('/v1/runs');const box=$('runs');box.replaceChildren();for(const r of [...(d.items||[]),...(d.jobs||[])].slice(0,12)){const b=document.createElement('button');b.className='secondary';b.style.margin='3px';b.textContent=(r.run_id||r.job_id)+' · '+r.status;b.onclick=()=>loadRun(r.run_id||r.job_id);box.appendChild(b)}if(!box.children.length)box.textContent='尚无运行记录'}
function svgNode(name,attrs={}){const n=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n}
async function renderGraph(id){const host=$('graph');host.replaceChildren();try{const response=await api('/v1/runs/'+encodeURIComponent(id)+'/graph');const g=response.graph;if(!g||!g.nodes?.length){host.textContent='当前运行没有可展示的图数据';return}const nodes=g.nodes.slice(0,16),edges=g.edges.slice(0,32),width=920,height=390,cx=width/2,cy=height/2,rx=width*0.39,ry=height*0.35;const positions=new Map();nodes.forEach((n,i)=>{const a=(2*Math.PI*i/Math.max(1,nodes.length))-Math.PI/2;positions.set(n.node_id,{x:cx+rx*Math.cos(a),y:cy+ry*Math.sin(a)})});const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,width:'100%',role:'img','aria-label':'Agent interaction graph'});const defs=svgNode('defs');const marker=svgNode('marker',{id:'arrow',viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});marker.appendChild(svgNode('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#64748b'}));defs.appendChild(marker);svg.appendChild(defs);edges.forEach(e=>{const a=positions.get(e.source),b=positions.get(e.target);if(!a||!b)return;const line=svgNode('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:e.risk_reasons?.length?'#dc2626':'#94a3b8','stroke-width':e.risk_reasons?.length?2.5:1.2,'marker-end':'url(#arrow)',opacity:0.85});const title=svgNode('title');title.textContent=`${e.event_type}: ${e.source} → ${e.target}; risk score=${e.risk_score}; ${e.risk_reasons||[]}`;line.appendChild(title);svg.appendChild(line)});nodes.forEach(n=>{const p=positions.get(n.node_id);const group=svgNode('g');const circle=svgNode('circle',{cx:p.x,cy:p.y,r:22,fill:n.node_type==='tool'?'#dbeafe':'#dcfce7',stroke:n.node_type==='tool'?'#2563eb':'#15803d','stroke-width':1.5});const title=svgNode('title');title.textContent=n.node_id;circle.appendChild(title);group.appendChild(circle);const text=svgNode('text',{x:p.x,y:p.y+39,'text-anchor':'middle','font-size':12,fill:'#334155'});text.textContent=n.node_id.length>18?n.node_id.slice(0,16)+'…':n.node_id;group.appendChild(text);svg.appendChild(group)});host.appendChild(svg);const summary=document.createElement('div');summary.className='muted';summary.textContent=`节点 ${g.node_count} · 边 ${g.edge_count} · 可疑边 ${g.suspicious_edge_count} · 候选路径 ${g.candidate_paths?.length||0}`;host.appendChild(summary);if(g.candidate_paths?.length){const list=document.createElement('ol');for(const path of g.candidate_paths.slice(0,5)){const li=document.createElement('li');li.textContent=`${path.nodes.join(' → ')} (score ${path.score.toFixed(2)})`;list.appendChild(li)}host.appendChild(list)}}catch(e){host.textContent=e.message}}
async function loadRun(id){try{let r=await api('/v1/runs/'+encodeURIComponent(id));if(r.job_id){if(['running','queued','cancel_requested'].includes(r.status)){show('summary',r);return}if(r.result_run_id){r=await api('/v1/runs/'+encodeURIComponent(r.result_run_id)+'/report')}}if(['running','queued','cancel_requested'].includes(r.status)){show('summary',r);return}currentRun=r.run_id;show('summary',{run_id:r.run_id,status:r.status,backend_mode:r.backend_mode,findings:r.findings?.length,hypotheses:r.hypotheses?.length,causal_claim_status:r.causal_claim_status});show('details',r);await renderGraph(currentRun)}catch(e){show('details',e.message)}}
$('run').onclick=async()=>{try{$('status').textContent='运行中…';const r=await api('/v1/runs',{method:'POST',body:JSON.stringify({scenario_id:$('scenario').value,runtime_backend:'mock',auto_intervene:false,max_interventions:1})});currentRun=r.run_id;show('summary',{run_id:r.run_id,status:r.status,backend_mode:r.backend_mode,findings:r.findings?.length,hypotheses:r.hypotheses?.length,causal_claim_status:r.causal_claim_status});show('details',r);await renderGraph(currentRun);await loadRuns();$('status').textContent='完成'}catch(e){$('status').textContent='失败';show('details',e.message)}};
$('refresh').onclick=()=>scenarios().catch(e=>show('details',e.message));$('loadRuns').onclick=()=>loadRuns().catch(e=>show('details',e.message));$('preview').onclick=async()=>{if(!currentRun)return;try{const d=await api('/v1/runs/'+currentRun+'/defenses/preview',{method:'POST',body:'{}'});show('details',d)}catch(e){show('details',e.message)}};$('evidence').onclick=async()=>{if(!currentRun)return;try{show('details',await api('/v1/runs/'+currentRun+'/evidence'))}catch(e){show('details',e.message)}};
Promise.all([scenarios(),loadCaps(),loadRuns()]).catch(e=>show('details',e.message));
</script></body></html>'''

app = create_app()
