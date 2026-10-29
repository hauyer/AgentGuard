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
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from agentguard import __version__
from agentguard.adapters.mock import MockBackend
from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status
from agentguard.agents.coordinator import AgentCoordinator
from agentguard.agents.llm_advisor import LLMInvestigationAdvisor
from agentguard.api.console import CONSOLE_HTML
from agentguard.api.public_routes import create_public_router
from agentguard.artifacts.local_store import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    LocalArtifactStore,
)
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
from agentguard.contracts.experiment_index import ExperimentCompareRequest
from agentguard.contracts.interaction_graph import InteractionGraphResponse
from agentguard.contracts.intervention import InterventionRequest
from agentguard.contracts.lifecycle import RunTimelineEvent
from agentguard.contracts.loop_public import (
    InvestigationLoopCreateRequest,
    InvestigationLoopReconciliationResponse,
)
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
    StudyRunRequest,
)
from agentguard.contracts.research_capability import ResearchCapabilityReport
from agentguard.contracts.stego_remediation import ReplicateBatchRequest
from agentguard.contracts.system_identity import SystemIdentityResponse
from agentguard.contracts.warden import WardenAuditRequest, WardenDefenseExecuteRequest
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
from agentguard.research_registry import IdempotencyConflictError, ResearchRegistry
from agentguard.services.evaluation_registry import EvaluationRegistry
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.experiment_index import ExperimentIndexService
from agentguard.services.interaction_graph import InteractionGraphService
from agentguard.services.investigation_loop import InvestigationLoopService
from agentguard.services.investigation_timeline import InvestigationTimelineService
from agentguard.services.loop_public import (
    LoopServiceError,
    PublicInvestigationLoopService,
)
from agentguard.services.loop_registry import InvestigationLoopRegistry
from agentguard.services.report_index import ReportIndexService
from agentguard.services.research_capability import build_research_capability_report
from agentguard.services.risk_findings import RiskFindingService
from agentguard.services.system_readiness import SystemReadinessService
from agentguard.services.warden_diagnostics import diagnose_kernel
from agentguard.services.warden_lifecycle import WardenLifecycleService
from agentguard.services.warden_workflow_store import WardenWorkflowStore, WorkflowConflictError

logger = logging.getLogger(__name__)
_MAX_API_REQUEST_BYTES = 16 * 1024 * 1024

_RESEARCH_ERROR_OBJECT_SCHEMA = {
    "type": "object",
    "required": ["detail"],
    "properties": {
        "detail": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "object"}},
                {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                        "code": {"type": "string"},
                        "request_id": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            ],
        },
    },
}


def _research_error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": _RESEARCH_ERROR_OBJECT_SCHEMA}},
    }


class DefenseExecuteRequest(ContractModel):
    candidate_id: str
    authorization_ref: str | None = None
    intervention_id: str | None = None


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

    @app.exception_handler(RequestValidationError)
    async def sanitized_request_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if any(
            error.get("type") == "missing"
            and error.get("loc") == ("header", "X-Loop-Idempotency-Key")
            for error in exc.errors()
        ):
            return JSONResponse(
                status_code=422,
                content={"detail": {
                    "error": "MISSING_IDEMPOTENCY_KEY",
                    "message": "X-Loop-Idempotency-Key header is required",
                }},
            )
        errors = [
            {
                "type": str(error.get("type", "validation_error")),
                "loc": list(error.get("loc", ())),
                "msg": "Invalid request",
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

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
    app.state.interaction_graph = InteractionGraphService()
    app.state.experiment_index = ExperimentIndexService(
        app.state.warden_workflows, app.state.artifacts
    )
    app.state.system_readiness = SystemReadinessService()
    app.state.investigation_timeline = InvestigationTimelineService(
        app.state.warden_workflows
    )
    app.state.report_index = ReportIndexService(app.state.artifacts)
    app.state.risk_findings = RiskFindingService(app.state.interaction_graph)
    app.state.loop_registry = InvestigationLoopRegistry(app.state.warden_workflows)
    app.state.public_loops = PublicInvestigationLoopService(
        loop=InvestigationLoopService(
            workflows=app.state.warden_workflows,
            artifacts=app.state.artifacts,
            evidence_gateway=app.state.evidence_gateway,
            experiment_index=app.state.experiment_index,
            risk_findings=app.state.risk_findings,
            experiences=app.state.experiences,
            observation=None,  # loop-created audits stay plan-gated in E2
        ),
        registry=app.state.loop_registry,
        artifacts=app.state.artifacts,
    )
    app.state.policy_engine = InterventionPolicyEngine()
    app.state.paired_runner = PairedExperimentRunner(app.state.engine.runtime, paired_executor=paired_executor)
    app.state.evaluation_registry = EvaluationRegistry(app.state.warden_workflows)

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
                "X-Loop-Idempotency-Key",
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
        supplied_approval = request.headers.get("X-Approval-Token", "")
        api_valid = bool(api_token and supplied_api and secrets.compare_digest(api_token, supplied_api))
        operator_valid = bool(
            operator_token and supplied_operator and secrets.compare_digest(operator_token, supplied_operator)
        )
        legacy_operator_valid = bool(
            not operator_token and api_token and supplied_api and secrets.compare_digest(api_token, supplied_api)
        )
        approval_token = os.environ.get("AGENTGUARD_APPROVAL_TOKEN", "")
        approval_valid = bool(
            approval_token and supplied_approval and secrets.compare_digest(approval_token, supplied_approval)
        )
        actor_role = "operator" if operator_valid or legacy_operator_valid or approval_valid else ("api" if api_valid else "anonymous")
        try:
            audit_event_id = await run_in_threadpool(
                app.state.warden_workflows.begin_api_audit_event,
                request_id=request_id, method=request.method, actor_role=actor_role,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed if the audit sink cannot reserve an event
            logger.error(
                "AgentGuard API audit reservation failed (request_id=%s, exception_type=%s)",
                request_id,
                type(exc).__name__,
            )
            response: Response = JSONResponse(
                status_code=503,
                content={"detail": {"error": "API_AUDIT_UNAVAILABLE", "request_id": request_id}},
            )
        else:
            request.state.audit_event_id = audit_event_id
            public_paths = {
                "/health", "/v1/system/health", "/v1/system/identity",
                "/openapi.json", "/docs", "/redoc", "/console",
            }
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
            content_length = request.headers.get("content-length")
            try:
                request_size = int(content_length) if content_length is not None else 0
            except ValueError:
                request_size = _MAX_API_REQUEST_BYTES + 1
            if request_size > _MAX_API_REQUEST_BYTES:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": {"error": "REQUEST_BODY_TOO_LARGE", "request_id": request_id}},
                )
            elif throttled:
                response = JSONResponse(
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
                except Exception as exc:  # noqa: BLE001 - sanitize any unexpected API failure at the boundary
                    logger.error(
                        "Unhandled AgentGuard API error (request_id=%s, exception_type=%s)",
                        request_id,
                        type(exc).__name__,
                    )
                    response = JSONResponse(
                        status_code=500,
                        content={"detail": {"error": "INTERNAL_SERVER_ERROR", "request_id": request_id}},
                    )
            try:
                matched_path = getattr(request.scope.get("route"), "path", "/<unmatched>")
                await run_in_threadpool(
                    app.state.warden_workflows.finish_api_audit_event,
                    audit_event_id, path=matched_path, status_code=response.status_code,
                )
            except Exception as exc:  # noqa: BLE001 - request processing must not expose audit storage failures
                logger.error(
                    "AgentGuard API audit event could not be completed "
                    "(request_id=%s, exception_type=%s)",
                    request_id,
                    type(exc).__name__,
                )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

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
        # Reports survive restarts as content-addressed artifacts; rehydrate a
        # validated copy instead of 404-ing a report the index still lists.
        rehydrated = _rehydrate_report(run_id)
        if rehydrated is not None:
            return rehydrated
        raise HTTPException(status_code=404, detail="run_id not found")

    def _rehydrate_report(run_id: str) -> RunReport | None:
        metadata = app.state.report_index.load_report_artifact(run_id)
        if metadata is None:
            return None
        try:
            payload = json.loads(app.state.artifacts.get_bytes(metadata.artifact_ref.artifact_id).decode("utf-8"))
            report = RunReport.model_validate(payload)
        except (ValueError, UnicodeDecodeError, OSError):
            return None
        if report.run_id != run_id:
            return None
        with app.state.lock:
            app.state.reports[run_id] = report
            app.state.run_artifacts[run_id] = metadata.artifact_ref.artifact_id
        return report

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

    def require_research_write_authorization(
        operator_token: str | None, approval_token: str | None,
    ) -> None:
        """Require an operator or approval credential for persisted study writes."""
        if approval_is_valid(approval_token):
            return
        require_operator_token(operator_token)

    def audit_research_write(
        http_request: Request,
        payload: Mapping[str, Any],
        *,
        approval_valid: bool,
    ) -> None:
        event_id = getattr(http_request.state, "audit_event_id", None)
        if not isinstance(event_id, int):
            raise HTTPException(status_code=503, detail={"error": "API_AUDIT_UNAVAILABLE"})
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        try:
            app.state.warden_workflows.record_api_audit_details(
                event_id,
                payload_sha256=hashlib.sha256(encoded).hexdigest(),
                approval_status="approval_authorized" if approval_valid else "operator_authorized",
            )
        except Exception as exc:
            logger.error(
                "AgentGuard API audit details could not be persisted (request_id=%s, exception_type=%s)",
                http_request.state.request_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503, detail={"error": "API_AUDIT_UNAVAILABLE", "request_id": http_request.state.request_id},
            ) from exc

    def require_operator_token(token: str | None) -> None:
        """Require the operator credential, with an explicit legacy API-token fallback."""
        expected = os.environ.get("AGENTGUARD_OPERATOR_API_TOKEN") or os.environ.get("AGENTGUARD_API_TOKEN", "")
        if not expected or not token or not secrets.compare_digest(expected, token):
            raise HTTPException(status_code=403, detail={"error": "OPERATOR_TOKEN_REQUIRED"})

    def require_dedicated_operator_token(token: str | None) -> None:
        """Require the dedicated Operator secret; API and approval credentials do not substitute."""
        expected = os.environ.get("AGENTGUARD_OPERATOR_API_TOKEN", "")
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

    @app.get("/v1/system/identity", response_model=SystemIdentityResponse)
    def get_system_identity() -> dict:
        """Expose build identity and a canonical hash of this runtime OpenAPI contract."""
        supplied_commit = os.environ.get("AGENTGUARD_BUILD_COMMIT", "").strip().lower()
        build_commit = supplied_commit if re.fullmatch(r"[0-9a-f]{40}", supplied_commit) else "unknown"
        openapi_bytes = json.dumps(
            app.openapi(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return SystemIdentityResponse(
            software_version=__version__,
            api_contract_version=__version__,
            build_commit=build_commit,
            build_commit_source=(
                "AGENTGUARD_BUILD_COMMIT" if build_commit != "unknown" else "unknown"
            ),
            runtime_identity_status=("declared" if build_commit != "unknown" else "unknown"),
            openapi_sha256=hashlib.sha256(openapi_bytes).hexdigest(),
            capabilities={
                "live_warden_loop": "blocked",
                "warden_remote_submission_idempotency": "unsupported",
                "warden_restart_durable_state": "unsupported",
                "warden_source_authenticity": "unverified",
                "storage_topology": "single_host",
            },
        ).model_dump(mode="json")

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

    @app.get("/v1/research/capabilities", response_model=ResearchCapabilityReport)
    def get_research_capabilities() -> ResearchCapabilityReport:
        """Inspect proposed research capabilities without creating a WARDEN plan or run."""
        try:
            client = _configured_warden_client()
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = detail.get("error") if isinstance(detail.get("error"), str) else "WARDEN_CONFIGURATION_ERROR"
            return build_research_capability_report({"status": "unavailable", "blockers": [code]})
        return build_research_capability_report(diagnose_kernel(client))

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
    def submit_kernel_audit(
        request: WardenAuditRequest,
        http_request: Request,
    ) -> dict:
        """Compatibility alias using the same durable workflow and approval gate."""
        result = execute_kernel_workflow(
            request, http_request,
        )
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
    def execute_kernel_workflow(
        request: WardenAuditRequest,
        http_request: Request,
    ) -> dict:
        """Preflight, submit and optionally wait for a WARDEN audit; persist evidence-gated memory."""
        if request.mode == "defense_evaluation" and not approval_is_valid(request.authorization_ref):
            raise HTTPException(
                status_code=403,
                detail={"error": "OPERATOR_APPROVAL_REQUIRED", "message": "defense_evaluation requires a valid AgentGuard approval reference"},
            )
        if request.research_protocol_ref is not None:
            require_research_write_authorization(
                http_request.headers.get("X-AgentGuard-Operator-Token")
                or http_request.headers.get("X-AgentGuard-Token"),
                http_request.headers.get("X-Approval-Token"),
            )
            audit_research_write(
                http_request, request.model_dump(mode="json"),
                approval_valid=approval_is_valid(http_request.headers.get("X-Approval-Token")),
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

    def get_interaction_graph(
        request_id: str,
        run_id: str | None = Query(default=None),
        include_findings: bool = Query(default=False),
    ) -> dict:
        """Project the recorded message-level interaction graph for one workflow.

        The WARDEN journal is authoritative for ``request_id``. When it holds no
        authorized message metadata the response reports ``available=false``; an
        explicitly requested synthetic ``run_id`` may be rendered through the same
        contract but is always labelled ``agentguard_synthetic_report``. With
        ``include_findings=true`` the metadata-based risk findings are analyzed
        and mounted onto the recorded edges as ids only.
        """
        entry = app.state.warden_workflows.get(request_id)
        graph: InteractionGraphResponse | None = None
        if entry is not None:
            graph = app.state.interaction_graph.for_warden_workflow(entry)
            if graph.available:
                if include_findings:
                    graph = _mount_findings(graph)
                return graph.model_dump(mode="json")
        if run_id is not None:
            with app.state.lock:
                report = app.state.reports.get(run_id)
            if report is None:
                raise HTTPException(status_code=404, detail={"error": "RUN_ID_NOT_FOUND"})
            fallback = app.state.interaction_graph.for_run_report(report, request_id=request_id)
            return fallback.model_dump(mode="json")
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})
        assert graph is not None
        return graph.model_dump(mode="json")

    def _mount_findings(graph: InteractionGraphResponse) -> InteractionGraphResponse:
        """Attach finding/hypothesis ids to the edges that evidence them."""
        report = app.state.risk_findings.analyze_graph(graph)
        finding_by_edge: dict[str, list[str]] = {}
        hypothesis_ids = {item.finding_id: item.hypothesis_id for item in report.hypotheses}
        for finding in report.findings:
            for ref in finding.supporting_evidence_refs:
                finding_by_edge.setdefault(ref, []).append(finding.finding_id)
        for edge in graph.edges:
            ids = finding_by_edge.get(edge.id, [])
            edge.finding_ids = ids
            edge.hypothesis_ids = [hypothesis_ids[i] for i in ids if i in hypothesis_ids]
        graph.limitations.extend(report.limitations)
        return graph

    def get_risk_analysis(request_id: str, run_id: str | None = Query(default=None)) -> dict:
        """Project the metadata-based risk analysis for one investigation workflow.

        Findings are structural signals over recorded interaction metadata;
        uncalibrated scores are never presented as attack probabilities and no
        causal claim is made. Without compliant metadata the response reports
        ``available=false`` with its limitations.
        """
        entry = app.state.warden_workflows.get(request_id)
        if entry is not None:
            report = app.state.risk_findings.analyze_workflow(entry)
            return report.model_dump(mode="json")
        if run_id is not None:
            with app.state.lock:
                engine_report = app.state.reports.get(run_id)
            if engine_report is None:
                raise HTTPException(status_code=404, detail={"error": "RUN_ID_NOT_FOUND"})
            graph = app.state.interaction_graph.for_run_report(
                engine_report, request_id=request_id,
            )
            analysis = app.state.risk_findings.analyze_graph(graph)
            return analysis.model_dump(mode="json")
        raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})

    def require_loop_creation_credential(
        x_agentguard_operator_token: str | None,
        x_approval_token: str | None,
    ) -> bool:
        """Creation authorization: operator credential or a valid approval token.

        A plain API token never grants loop creation, and client role claims
        are ignored; the check is against server-configured credentials only.
        """
        operator_expected = os.environ.get("AGENTGUARD_OPERATOR_API_TOKEN", "")
        approval_expected = os.environ.get("AGENTGUARD_APPROVAL_TOKEN", "")
        operator_ok = bool(
            operator_expected and x_agentguard_operator_token
            and secrets.compare_digest(operator_expected, x_agentguard_operator_token)
        )
        approval_ok = bool(
            approval_expected and x_approval_token
            and secrets.compare_digest(approval_expected, x_approval_token)
        )
        return operator_ok or approval_ok

    def create_investigation_loop(
        request: InvestigationLoopCreateRequest,
        x_agentguard_operator_token: str | None = Header(
            default=None, alias="X-AgentGuard-Operator-Token",
        ),
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
        x_loop_idempotency_key: str = Header(..., alias="X-Loop-Idempotency-Key"),
    ) -> dict:
        """Run a multi-round investigation loop over a finished base workflow.

        Requires the operator credential or a valid approval token and a
        mandatory ``X-Loop-Idempotency-Key`` header. Registration always
        precedes execution so a crash or restart leaves a recoverable record
        rather than a duplicate external audit.
        """
        authorized = require_loop_creation_credential(
            x_agentguard_operator_token, x_approval_token,
        )
        if not x_loop_idempotency_key or not x_loop_idempotency_key.strip():
            raise HTTPException(
                status_code=422,
                detail={"error": "MISSING_IDEMPOTENCY_KEY",
                        "message": "X-Loop-Idempotency-Key header is required"},
            )
        try:
            return app.state.public_loops.create_with_idempotency_key(
                request,
                idempotency_key=x_loop_idempotency_key.strip(),
                authorization_valid=authorized,
            )
        except LoopServiceError as exc:
            code = str(exc)
            status = {
                "LOOP_AUTHORIZATION_REQUIRED": 403,
                "BASE_INVESTIGATION_NOT_FOUND": 404,
                "BASE_INVESTIGATION_NOT_FINISHED": 409,
                "LOOP_IDEMPOTENCY_CONFLICT": 409,
                "LOOP_EXECUTION_FAILED": 502,
            }.get(code, 400)
            raise HTTPException(status_code=status, detail={"error": code}) from exc
        except ValueError as exc:
            # Registry conflicts surface as bare ValueError.
            raise HTTPException(
                status_code=409, detail={"error": "LOOP_IDEMPOTENCY_CONFLICT"},
            ) from exc

    def list_investigation_loops(
        base_request_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        return app.state.public_loops.list_loops(
            base_request_id=base_request_id, limit=limit, offset=offset,
        )

    def get_investigation_loop(loop_id: str) -> dict:
        try:
            return app.state.public_loops.get_view(loop_id)
        except LoopServiceError:
            raise HTTPException(status_code=404, detail={"error": "LOOP_NOT_FOUND"}) from None

    def list_loop_decisions(loop_id: str) -> dict:
        try:
            return app.state.public_loops.decisions(loop_id)
        except LoopServiceError:
            raise HTTPException(status_code=404, detail={"error": "LOOP_NOT_FOUND"}) from None

    def list_loop_evidence_changes(loop_id: str) -> dict:
        try:
            return app.state.public_loops.evidence_changes(loop_id)
        except LoopServiceError:
            raise HTTPException(status_code=404, detail={"error": "LOOP_NOT_FOUND"}) from None

    def get_loop_budget(loop_id: str) -> dict:
        try:
            return app.state.public_loops.budget(loop_id)
        except LoopServiceError:
            raise HTTPException(status_code=404, detail={"error": "LOOP_NOT_FOUND"}) from None

    @app.post(
        "/v1/investigation-loops/{loop_id}/reconcile",
        response_model=InvestigationLoopReconciliationResponse,
        responses={
            403: {"description": "A dedicated Operator credential is required."},
            404: {"description": "The Loop does not exist."},
        },
    )
    def reconcile_investigation_loop(
        loop_id: str,
        x_agentguard_operator_token: str | None = Header(
            default=None, alias="X-AgentGuard-Operator-Token",
        ),
    ) -> dict:
        """Query existing remote operations and update local facts; never resubmit an action."""
        require_dedicated_operator_token(x_agentguard_operator_token)
        client = _configured_warden_client()
        if client is None:
            return app.state.public_loops.reconcile_loop(loop_id)
        service = WardenLifecycleService(
            client,
            experiences=app.state.experiences,
            workflows=app.state.warden_workflows,
        )
        try:
            return app.state.public_loops.reconcile_loop(
                loop_id, reconcile_workflow=service.reconcile_existing,
            )
        except LoopServiceError:
            raise HTTPException(status_code=404, detail={"error": "LOOP_NOT_FOUND"}) from None

    def list_strategy_evaluations(
        data_partition: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        """List persisted strategy evaluation reports from the durable index."""
        entries = app.state.evaluation_registry.list(
            data_partition=data_partition, limit=limit, offset=offset,
        )
        return {
            "schema_version": "1.0",
            "items": entries,
            "count": len(entries),
            "offset": offset,
            "limit": limit,
        }

    def get_strategy_evaluation(evaluation_id: str) -> dict:
        """Fetch one persisted strategy evaluation report by ID."""
        entry = app.state.evaluation_registry.get(evaluation_id)
        if entry is None:
            raise HTTPException(status_code=404, detail={"error": "EVALUATION_NOT_FOUND"})
        try:
            payload = app.state.artifacts.get_bytes(entry["report_artifact_id"])
            import json
            report = json.loads(payload.decode("utf-8"))
        except (ValueError, OSError):
            raise HTTPException(
                status_code=410, detail={"error": "EVALUATION_ARTIFACT_UNAVAILABLE"},
            ) from None
        # Ensure the report view includes the actual artifact_id from the index,
        # since the artifact JSON may have been serialized before the id was assigned.
        report["report_artifact_id"] = entry["report_artifact_id"]
        return report


    def list_experiments(
        status: str | None = None,
        scenario_id: str | None = None,
        data_source: str | None = Query(default=None, pattern="^(warden_workflow|synthetic_report)$"),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        """Index persisted WARDEN workflows and engine run reports; no fabricated metrics."""
        result = app.state.experiment_index.list_experiments(
            status=status, scenario_id=scenario_id, data_source=data_source,
            limit=limit, offset=offset,
        )
        return result.model_dump(mode="json")

    def get_experiment(experiment_id: str) -> dict:
        """Resolve one indexed experiment by experiment_id, request_id or run_id."""
        detail = app.state.experiment_index.get_experiment(experiment_id)
        if detail is None:
            raise HTTPException(status_code=404, detail={"error": "EXPERIMENT_NOT_FOUND"})
        return detail.model_dump(mode="json")

    def compare_experiments(request: ExperimentCompareRequest) -> dict:
        """Descriptively compare two existing run reports after comparability checks."""
        result = app.state.experiment_index.compare(list(request.run_ids))
        return result.model_dump(mode="json")

    def get_system_readiness(force_refresh: bool = Query(default=False)) -> dict:
        """Layered readiness; WARDEN health is never promoted to executability."""
        result = app.state.system_readiness.readiness(force_refresh=force_refresh)
        return result.model_dump(mode="json")

    def get_investigation_options(force_refresh: bool = Query(default=False)) -> dict:
        """Real audit-mode options and resources for the investigation form."""
        result = app.state.system_readiness.investigation_options(
            force_refresh=force_refresh,
        )
        return result.model_dump(mode="json")

    def get_investigation_timeline(
        request_id: str, after_event_id: int | None = Query(default=None, ge=1)
    ) -> dict:
        """Project the durable journal into a real timeline; never synthesize steps."""
        timeline = app.state.investigation_timeline.timeline(
            request_id, after_event_id=after_event_id
        )
        if timeline is None:
            raise HTTPException(status_code=404, detail={"error": "WORKFLOW_NOT_FOUND"})
        return timeline.model_dump(mode="json")

    def list_reports(
        status: str | None = None,
        scenario_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        """Index persisted run-report artifacts; artifact ids only, never paths."""
        return app.state.report_index.list_reports(
            status=status, scenario_id=scenario_id, limit=limit, offset=offset,
        )


    def _annotate_defense_capabilities(
        preview: DefensePreview,
        *,
        verification=None,
        verified_candidate_ids: tuple[str, ...] = (),
    ) -> DefensePreview:
        """Fill the C6 capability fields without touching lifecycle semantics.

        Approval validity, executor availability and verification evidence are
        independent; none is inferred from another.
        """
        preview.execution_supported_overall = False
        preview.rollback_supported = False
        preview.approval_required_per_candidate = any(
            candidate.approval_required for candidate in preview.candidates
        )
        verified_available = bool(verified_candidate_ids) or (
            verification is not None
            and getattr(verification, "provenance_verified", False) is True
        )
        preview.verification_evidence_available = verified_available
        preview.benign_utility_data_available = bool(
            verification is not None
            and getattr(verification, "provenance_verified", False) is True
            and getattr(verification, "benign_utility_before", None) is not None
        )
        preview.capability_notes = [
            "No external defense executor is declared; approval alone cannot enable execution (DEP-007).",
            "No public rollback API exists; rollback stays unsupported (DEP-003).",
            (
                "Provenance-verified defense verification evidence exists for this run."
                if verified_available else
                "No provenance-verified defense verification evidence exists for this run."
            ),
        ]
        return preview

    def _build_warden_defense_preview(request_id: str, persist: bool) -> DefensePreview:
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
        verified_ids = tuple(
            item["candidate"]["candidate_id"]
            for item in app.state.warden_workflows.list_defense_candidates(request_id)
            if item["lifecycle"]["status"] == "verified"
        )
        return _annotate_defense_capabilities(preview, verified_candidate_ids=verified_ids)

    @app.get("/v1/kernel/workflows/{request_id}/defenses/preview")
    def get_warden_defense_preview(request_id: str) -> dict:
        return _build_warden_defense_preview(request_id, persist=False).model_dump(mode="json")

    @app.post("/v1/kernel/workflows/{request_id}/defenses/preview")
    def create_warden_defense_preview(request_id: str) -> dict:
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
        return _annotate_defense_capabilities(
            preview, verification=report.defense_verification
        ).model_dump(mode="json")

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
        return _annotate_defense_capabilities(
            preview, verification=report.defense_verification
        ).model_dump(mode="json")

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

    @app.post(
        "/v1/studies", response_model=RegisteredStudy, status_code=201,
        responses={
            401: _research_error_response("Global API token is required when configured."),
            403: _research_error_response("A configured operator or approval credential is required."),
            409: _research_error_response("Idempotency-Key conflicts or study registration could not be persisted."),
            413: _research_error_response("Request body exceeds the configured 16 MiB limit."),
            422: _research_error_response("Study registration contract validation failed."),
            429: _research_error_response("The API request rate limit was exceeded."),
            500: _research_error_response("An unexpected API error occurred."),
            503: _research_error_response("The API audit record could not be persisted."),
        },
    )
    def register_study(
        request: StudyRegistrationRequest,
        http_request: Request,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", min_length=1, max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
        x_agentguard_operator_token: str | None = Header(default=None, alias="X-AgentGuard-Operator-Token"),
        x_agentguard_token: str | None = Header(default=None, alias="X-AgentGuard-Token"),
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> RegisteredStudy:
        """Create an immutable preregistration before inspecting study outcomes."""
        require_research_write_authorization(
            x_agentguard_operator_token or x_agentguard_token, x_approval_token,
        )
        audit_research_write(
            http_request, request.model_dump(mode="json"),
            approval_valid=approval_is_valid(x_approval_token),
        )
        actor_role = "approval" if approval_is_valid(x_approval_token) else "operator"
        try:
            return app.state.research_registry.register(
                request, idempotency_key=idempotency_key, actor_role=actor_role,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail={"error": "IDEMPOTENCY_CONFLICT", "message": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail={"error": "STUDY_REGISTRATION_FAILED", "code": type(exc).__name__}) from exc

    @app.get("/v1/studies")
    def list_studies(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        track: Literal["mechanism_testbed", "live_steganography"] | None = None,
        sort_by: Literal["registered_at", "title"] = "registered_at",
        sort_order: Literal["asc", "desc"] = "desc",
    ) -> dict:
        registry = app.state.research_registry
        items = registry.list(
            limit=limit, offset=offset, track=track, sort_by=sort_by, sort_order=sort_order,
        )
        total = registry.study_count(track=track)
        next_offset = offset + len(items) if offset + len(items) < total else None
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": total, "limit": limit, "offset": offset, "next_offset": next_offset,
            "track": track, "sort_by": sort_by, "sort_order": sort_order,
        }

    from agentguard.contracts.experiment_view import ExperimentTimeline, ExperimentVisualization

    @app.get("/v1/studies/{study_id}/runs/{workflow_id}/visualization", response_model=ExperimentVisualization)
    def study_visualization(study_id: str, workflow_id: str,
                            through_seq: int | None = Query(default=None, ge=0)) -> dict:
        from agentguard.services.experiment_views import project_graph
        from agentguard.services.study_execution import StudyExecutionService

        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            result, events = StudyExecutionService(app.state.research_registry,
                app.state.warden_workflows, client, app.state.artifacts).events(study_id, workflow_id)
            study = app.state.research_registry.get(study_id)
            if through_seq is not None and through_seq > len(events):
                raise HTTPException(status_code=422, detail="INVALID_EVENT_CURSOR")
            manifest = next((item["manifest"] for item in result.get("artifacts", [])), {})
            return {"schema_version": "stego.visualization/1",
                "data_mode": "RECORDED" if events else "UNAVAILABLE",
                "origin_mode": "LIVE_S" if events else "LIVE_M" if manifest else "UNAVAILABLE",
                "workflow_id": workflow_id, "status": (result.get("run") or {}).get("status", result.get("status")),
                "graph": project_graph(study_id, (result.get("run") or {}).get("run_id"),
                    study.track or "mechanism_testbed", events, through_seq=through_seq),
                "events": events, "last_seq": len(events), "metrics": manifest.get("metrics", {}),
                "artifacts": [{"artifact_id": item["artifact_id"], "sha256": item["sha256"]}
                              for item in result.get("artifacts", [])],
                "unavailable_reason": None if events else "NO_OBSERVED_COMMUNICATION_EVENTS",
                "limitations": manifest.get("limitations", ["M track capture is not S communication."])}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study run not found") from exc
        except (ValueError, WardenKernelError) as exc:
            raise HTTPException(status_code=502, detail={"error": "EXPERIMENT_VIEW_NOT_VERIFIED"}) from exc

    @app.get("/v1/studies/{study_id}/runs/{workflow_id}/timeline", response_model=ExperimentTimeline)
    def study_timeline(study_id: str, workflow_id: str,
                       after_seq: int = Query(default=0, ge=0),
                       limit: int = Query(default=100, ge=1, le=200)) -> dict:
        view = study_visualization(study_id, workflow_id, None)
        if after_seq > view["last_seq"]:
            raise HTTPException(status_code=422, detail="INVALID_EVENT_CURSOR")
        events = [event for event in view["events"] if event["seq"] > after_seq][:limit]
        return {"schema_version": "stego.timeline/1", "events": events,
                "status": view["status"],
                "next_seq": events[-1]["seq"] if events else after_seq,
                "last_seq": view["last_seq"], "has_more": bool(events and events[-1]["seq"] < view["last_seq"]),
                "unavailable_reason": view["unavailable_reason"]}

    @app.get("/v1/studies/{study_id}/comparison")
    def study_comparison(study_id: str) -> dict:
        from agentguard.services.experiment_views import compare_verified_runs
        from agentguard.services.study_execution import StudyExecutionService

        study = app.state.research_registry.get(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="Study not found")
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            service = StudyExecutionService(app.state.research_registry,
                app.state.warden_workflows, client, app.state.artifacts)
            entries = app.state.warden_workflows.list_for_study(study_id)
            results = [service.result(study_id, entry["request_id"]) for entry in entries]
            return compare_verified_runs(study, results)
        except (ValueError, WardenKernelError) as exc:
            raise HTTPException(status_code=502, detail={"error": "PAIRED_RESULTS_NOT_VERIFIED"}) from exc

    @app.get("/v1/studies/{study_id}/comparisons/batches/{batch_id}")
    def study_batch_comparison(study_id: str, batch_id: str) -> dict:
        from agentguard.services.study_execution import StudyExecutionService
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error":"WARDEN_NOT_CONFIGURED"})
        try:
            return StudyExecutionService(app.state.research_registry, app.state.warden_workflows,
                client, app.state.artifacts).compare_batch(study_id, batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study batch not found") from exc
        except (ValueError, WardenKernelError, ArtifactIntegrityError, ArtifactNotFoundError) as exc:
            raise HTTPException(status_code=502, detail={"error":"BATCH_COMPARISON_NOT_VERIFIED"}) from exc

    @app.get("/v1/studies/{study_id}/submission-intents/{intent_key}")
    def lookup_submission_intent(study_id: str, intent_key: str) -> dict:
        from agentguard.services.study_execution import StudyExecutionService
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",intent_key):
            raise HTTPException(status_code=422,detail="Invalid submission intent")
        try:
            return StudyExecutionService(app.state.research_registry,app.state.warden_workflows,
                None,app.state.artifacts).lookup_intent(study_id,intent_key)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail="Study not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502,detail="SUBMISSION_INTENT_NOT_VERIFIED") from exc

    @app.get("/v1/studies/{study_id}/arms/{arm_id}/execution-contract")
    def study_execution_contract(study_id: str,arm_id: str) -> dict:
        from agentguard.services.study_execution import StudyExecutionService
        client=_configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503,detail="WARDEN_NOT_CONFIGURED")
        try:
            return StudyExecutionService(app.state.research_registry,app.state.warden_workflows,
                client,app.state.artifacts).execution_contract(study_id,arm_id)
        except KeyError as exc:
            raise HTTPException(status_code=404,detail="Study not found") from exc
        except (ValueError,WardenKernelError) as exc:
            raise HTTPException(status_code=502,detail="FROZEN_EXECUTION_CONTRACT_NOT_VERIFIED") from exc

    @app.post("/v1/studies/{study_id}/replicate-batches")
    def submit_replicate_batch(study_id: str, request: ReplicateBatchRequest, http_request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128,
                                     pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")) -> dict:
        from agentguard.services.study_execution import StudyExecutionService
        require_research_write_authorization(
            http_request.headers.get("X-AgentGuard-Operator-Token")
            or http_request.headers.get("X-AgentGuard-Token"),
            http_request.headers.get("X-Approval-Token"))
        audit_research_write(http_request, {"study_id":study_id,"replicate_id":request.replicate_id},
                             approval_valid=False)
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error":"WARDEN_NOT_CONFIGURED"})
        try:
            return StudyExecutionService(app.state.research_registry, app.state.warden_workflows,
                client, app.state.artifacts).submit_batch(study_id, idempotency_key,
                                                         replicate_id=request.replicate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        except (WorkflowConflictError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/studies/{study_id}/run-batches")
    def list_study_batches(study_id: str, limit: int = Query(default=50, ge=1, le=100),
                          offset: int = Query(default=0, ge=0)) -> dict:
        from agentguard.artifacts.local_store import ArtifactIntegrityError, ArtifactNotFoundError
        from agentguard.services.study_execution import StudyExecutionService
        try:
            return StudyExecutionService(app.state.research_registry, app.state.warden_workflows,
                None, app.state.artifacts).list_batches(study_id, limit=limit, offset=offset)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        except (ValueError, ArtifactIntegrityError, ArtifactNotFoundError) as exc:
            raise HTTPException(status_code=502, detail="BATCH_MANIFEST_NOT_VERIFIED") from exc

    @app.post("/v1/studies/{study_id}/run-batches")
    def submit_study_batch(study_id: str, http_request: Request,
                          idempotency_key: str = Header(alias="Idempotency-Key", min_length=1,
                              max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")) -> dict:
        from agentguard.services.study_execution import StudyExecutionService

        require_research_write_authorization(
            http_request.headers.get("X-AgentGuard-Operator-Token")
            or http_request.headers.get("X-AgentGuard-Token"),
            http_request.headers.get("X-Approval-Token"))
        audit_research_write(http_request, {"study_id": study_id, "batch": True}, approval_valid=False)
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return StudyExecutionService(app.state.research_registry, app.state.warden_workflows,
                client, app.state.artifacts).submit_batch(study_id, idempotency_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/studies/{study_id}/runs")
    def list_study_runs(study_id: str) -> dict:
        if app.state.research_registry.get(study_id) is None:
            raise HTTPException(status_code=404, detail="Study not found")
        return {"items": [
            {"workflow_id": entry["request_id"], "run_id": entry.get("run_id"),
             "arm_id":entry.get("request",{}).get("study_arm_id"),
             "batch_id":entry.get("request",{}).get("metadata",{}).get("batch_id"),
             "replicate_id":entry.get("request",{}).get("metadata",{}).get("replicate_id"),
             "status": entry["workflow_status"], "updated_at": entry.get("updated_at")}
            for entry in app.state.warden_workflows.list_for_study(study_id)
        ]}

    @app.post("/v1/studies/{study_id}/runs")
    def submit_study_run(
        study_id: str, request: StudyRunRequest, http_request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128,
                                      pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
    ) -> dict:
        from agentguard.services.study_execution import StudyExecutionService

        require_research_write_authorization(
            http_request.headers.get("X-AgentGuard-Operator-Token")
            or http_request.headers.get("X-AgentGuard-Token"),
            http_request.headers.get("X-Approval-Token"),
        )
        audit_research_write(http_request, {"study_id": study_id, "arm_id": request.arm_id},
                             approval_valid=False)
        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return StudyExecutionService(
                app.state.research_registry, app.state.warden_workflows, client, app.state.artifacts,
            ).submit(study_id, request.arm_id, idempotency_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except WardenKernelError as exc:
            raise HTTPException(status_code=502, detail={"error": exc.code}) from exc

    @app.get("/v1/studies/{study_id}/runs/{workflow_id}")
    def study_run_result(study_id: str, workflow_id: str) -> dict:
        from agentguard.services.study_execution import StudyExecutionService

        client = _configured_warden_client()
        if client is None:
            raise HTTPException(status_code=503, detail={"error": "WARDEN_NOT_CONFIGURED"})
        try:
            return StudyExecutionService(
                app.state.research_registry, app.state.warden_workflows, client, app.state.artifacts,
            ).result(study_id, workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Study run not found") from exc
        except (ValueError, WardenKernelError) as exc:
            raise HTTPException(status_code=502, detail={"error": "STUDY_RESULT_NOT_VERIFIED"}) from exc

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

    @app.post(
        "/v1/studies/{study_id}/analyses", response_model=StudyAnalysisReport, status_code=201,
        responses={
            400: _research_error_response("Study analysis input or preregistered comparison is invalid."),
            401: _research_error_response("Global API token is required when configured."),
            403: _research_error_response("A configured operator or approval credential is required."),
            404: _research_error_response("The study does not exist."),
            409: _research_error_response("Idempotency-Key conflicts with a previous request."),
            413: _research_error_response("Request body exceeds the configured 16 MiB limit."),
            422: _research_error_response("Measured outcomes are rejected until WARDEN artifacts are verified."),
            429: _research_error_response("The API request rate limit was exceeded."),
            500: _research_error_response("An unexpected API error occurred."),
            503: _research_error_response("The API audit record could not be persisted."),
        },
    )
    def analyze_study(
        study_id: str,
        request: StudyAnalysisRequest,
        http_request: Request,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", min_length=1, max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
        x_agentguard_operator_token: str | None = Header(default=None, alias="X-AgentGuard-Operator-Token"),
        x_agentguard_token: str | None = Header(default=None, alias="X-AgentGuard-Token"),
        x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
    ) -> StudyAnalysisReport:
        require_research_write_authorization(
            x_agentguard_operator_token or x_agentguard_token, x_approval_token,
        )
        audit_research_write(
            http_request, {"study_id": study_id, "analysis": request.model_dump(mode="json")},
            approval_valid=approval_is_valid(x_approval_token),
        )
        study = app.state.research_registry.get(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study_id not found")
        if request.evidence_mode == "measured":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "ARTIFACT_CONTENT_NOT_VERIFIED",
                    "message": "measured outcomes require a verified WARDEN run and hash-checked artifact import",
                },
            )
        actor_role = "approval" if approval_is_valid(x_approval_token) else "operator"
        request_payload = request.model_dump(mode="json")
        try:
            if idempotency_key is not None:
                previous = app.state.research_registry.get_idempotent_analysis(
                    study_id, idempotency_key, request_payload,
                )
                if previous is not None:
                    return previous
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
            return app.state.research_registry.add_analysis(
                analysis, idempotency_key=idempotency_key,
                request_payload=request_payload if idempotency_key is not None else None,
                actor_role=actor_role,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail={"error": "IDEMPOTENCY_CONFLICT", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "STUDY_ANALYSIS_INVALID", "message": str(exc)}) from exc

    @app.get("/v1/studies/{study_id}/analyses")
    def list_study_analyses(
        study_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        evidence_mode: Literal["synthetic", "script_replay", "observed", "measured"] | None = None,
        sort_order: Literal["asc", "desc"] = "asc",
    ) -> dict:
        if app.state.research_registry.get(study_id) is None:
            raise HTTPException(status_code=404, detail="study_id not found")
        registry = app.state.research_registry
        items = registry.analyses(
            study_id, limit=limit, offset=offset,
            evidence_mode=evidence_mode, sort_order=sort_order,
        )
        total = registry.analysis_count(study_id, evidence_mode=evidence_mode)
        next_offset = offset + len(items) if offset + len(items) < total else None
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": total, "limit": limit, "offset": offset, "next_offset": next_offset,
            "evidence_mode": evidence_mode, "sort_order": sort_order,
        }

    app.include_router(create_public_router(
        health_handler=health,
        capabilities_handler=get_capabilities,
        create_workflow_handler=execute_kernel_workflow,
        list_workflows_handler=list_kernel_workflows,
        get_workflow_handler=get_kernel_workflow,
        get_evidence_handler=get_kernel_workflow_evidence,
        build_defense_preview_handler=_build_warden_defense_preview,
        execute_defense_handler=execute_warden_defense_proposal,
        get_report_handler=get_run_report,
        get_interaction_graph_handler=get_interaction_graph,
        list_experiments_handler=list_experiments,
        get_experiment_handler=get_experiment,
        compare_experiments_handler=compare_experiments,
        get_system_readiness_handler=get_system_readiness,
        get_investigation_options_handler=get_investigation_options,
        get_investigation_timeline_handler=get_investigation_timeline,
        list_reports_handler=list_reports,
        get_risk_analysis_handler=get_risk_analysis,
        create_loop_handler=create_investigation_loop,
        list_loops_handler=list_investigation_loops,
        get_loop_handler=get_investigation_loop,
        list_loop_decisions_handler=list_loop_decisions,
        list_loop_evidence_changes_handler=list_loop_evidence_changes,
        get_loop_budget_handler=get_loop_budget,
        list_strategy_evaluations_handler=list_strategy_evaluations,
        get_strategy_evaluation_handler=get_strategy_evaluation,
    ))
    from agentguard.api.graded_security import register_graded_routes
    from agentguard.services.study_execution import StudyExecutionService

    register_graded_routes(app, lambda: StudyExecutionService(app.state.research_registry,
        app.state.warden_workflows, _configured_warden_client(), app.state.artifacts),
        require_research_write_authorization, audit_research_write)
    return app




app = create_app()
