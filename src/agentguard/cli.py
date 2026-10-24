"""AgentGuard command-line interface."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import hmac
from pathlib import Path
import sys
from typing import Any
import uvicorn

from agentguard import __version__
from agentguard.adapters.mock import MockBackend
from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.core.orchestrator import AgentGuardEngine
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.memory.store import ExperienceStore
from agentguard.contracts.research import StudyRegistrationRequest, StudyAnalysisRequest
from agentguard.research_registry import ResearchRegistry
from agentguard.core.study_analysis import analyze_registered_study
from agentguard.contracts.attribution import AttributionNodeAnalysisRequest, PathShiftAnalysisRequest
from agentguard.core.attribution import analyze_attribution_node, analyze_path_shift
from agentguard.agents.llm_advisor import LLMInvestigationAdvisor
from agentguard.agents.model_client import ModelClientError
from agentguard.agents.coordinator import AgentCoordinator
from agentguard.core.graph import MultiLayerDetector
from agentguard.core.planner import InvestigationPlanner, DefensePlanner
from agentguard.core.experience_bandit import recommend_actions
from agentguard.services.warden_lifecycle import WardenLifecycleService
from agentguard.services.warden_workflow_store import WardenWorkflowStore
from agentguard.services.warden_diagnostics import diagnose_kernel


def _build_engine(*, use_llm_investigator: bool = False) -> tuple[AgentGuardEngine, MockBackend]:
    backend = MockBackend()
    if not use_llm_investigator:
        return AgentGuardEngine(backend, backend, backend), backend
    advisor = LLMInvestigationAdvisor.from_env()
    detector = MultiLayerDetector()
    investigator = InvestigationPlanner()
    defense_planner = DefensePlanner()
    coordinator = AgentCoordinator(
        detector=detector, investigator=investigator, defense_planner=defense_planner, advisor=advisor
    )
    return AgentGuardEngine(
        backend, backend, backend, detector=detector, investigator=investigator,
        defense_planner=defense_planner, coordinator=coordinator,
    ), backend


def _data_dir() -> Path:
    return Path(os.environ.get("AGENTGUARD_DATA_DIR", ".agentguard")).expanduser().resolve()


def _save_report(report, output: str | None = None) -> Path:
    destination = Path(output).expanduser() if output else _data_dir() / "runs" / f"{report.run_id}.json"
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return destination


def _load_request_config(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(raw)
    else:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML config requires PyYAML; use JSON or install PyYAML.") from exc
        value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError("Config root must be an object")
    return value.get("experiment", value)


def _find_report(path_or_id: str) -> dict[str, Any]:
    path = Path(path_or_id).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    candidate = _data_dir() / "runs" / f"{path_or_id}.json"
    if not candidate.is_file():
        raise FileNotFoundError(f"No saved report for run_id/path: {path_or_id}")
    return json.loads(candidate.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentguard", description="AgentGuard research and defense toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the synthetic end-to-end demo")
    demo.add_argument("--scenario", default="demo_cloud_security")
    demo.add_argument("--max-interventions", type=int, default=1)
    demo.add_argument("--no-auto-intervene", action="store_true", help="propose but do not execute an intervention")
    demo.add_argument("--output", help="optional JSON report path")
    demo.add_argument("--llm-investigator", action="store_true", help="opt in to external LLM candidate prioritization; no tools or execution authority")

    run = sub.add_parser("run", help="run an experiment from a JSON/YAML request config")
    run.add_argument("--config", required=True)
    run.add_argument("--output")
    run.add_argument("--no-auto-intervene", action="store_true")
    run.add_argument("--llm-investigator", action="store_true", help="opt in to external LLM candidate prioritization; no tools or execution authority")

    sub.add_parser("doctor", help="check local framework and optional integrations")
    sub.add_parser("capabilities", help="print configured runtime capabilities")
    scenarios = sub.add_parser("scenarios", help="list or show public scenarios")
    scenarios.add_argument("action", choices=["list", "show"], nargs="?", default="list")
    scenarios.add_argument("scenario_id", nargs="?")

    runs = sub.add_parser("runs", help="inspect saved run reports")
    runs.add_argument("action", choices=["status", "report"])
    runs.add_argument("run_id_or_path")

    adapters = sub.add_parser("adapters", help="inspect adapter availability")
    adapters.add_argument("action", choices=["check"], default="check", nargs="?")

    artifacts = sub.add_parser("artifacts", help="inspect content-addressed artifacts")
    artifacts.add_argument("action", choices=["list"], default="list", nargs="?")
    artifacts.add_argument("--run-id")

    benchmark = sub.add_parser("benchmark", help="compare saved reports")
    benchmark.add_argument("action", choices=["compare"])
    benchmark.add_argument("--runs", required=True, help="comma-separated run IDs or JSON paths")

    experience = sub.add_parser("experience", help="query the versioned experience store")
    experience.add_argument("action", choices=["search", "stats", "recommend"], default="search", nargs="?")
    experience.add_argument("query", nargs="?", default="")
    experience.add_argument("--scenario-id")
    experience.add_argument("--risk-type")
    experience.add_argument("--verified-only", action="store_true")
    experience.add_argument("--seed", type=int, default=7)
    experience.add_argument("--limit", type=int, default=5)

    study = sub.add_parser("study", help="immutable preregistration and family-aware statistical analysis")
    study.add_argument("action", choices=["register", "list", "show", "integrity", "analyze", "analyses"])
    study.add_argument("--study-id")
    study.add_argument("--config", help="JSON preregistration request")
    study.add_argument("--data", help="JSON paired outcome arrays for preregistered analysis")
    study.add_argument("--output", help="optional JSON output path")

    kernel = sub.add_parser("kernel", help="inspect or call the authoritative WARDEN audit kernel")
    kernel.add_argument("action", choices=["status", "doctor", "capabilities", "resources", "contracts", "submit", "run", "workflow-status"])
    kernel.add_argument("--request-id", help="AgentGuard workflow request ID (workflow-status only)")
    kernel.add_argument("--kind", choices=["probe", "sae"], help="filter WARDEN resources")
    kernel.add_argument("--contract-id", help="required WARDEN threat contract (doctor only)")
    kernel.add_argument("--probe-id", help="required ready Probe resource (doctor only)")
    kernel.add_argument("--sae-id", help="required ready SAE resource (doctor only)")
    kernel.add_argument(
        "--mode",
        choices=["observation", "capture_only", "detection", "causal_audit", "defense_evaluation"],
        default="causal_audit",
        help="audit mode for kernel doctor",
    )
    kernel.add_argument("--config", help="JSON WARDEN audit request for the submit action")
    kernel.add_argument("--plan-only", action="store_true", help="create a plan but do not submit a run")
    kernel.add_argument("--wait", action="store_true", help="wait for a terminal WARDEN run status (run action only)")
    kernel.add_argument("--timeout", type=float, help="bounded wait timeout in seconds (1..120)")

    agents = sub.add_parser("agents", help="inspect optional LLM-backed research agent configuration")
    agents.add_argument("action", choices=["status"], default="status", nargs="?")

    attribution = sub.add_parser("attribution", help="analyze candidate-node mediation or path migration")
    attribution.add_argument("action", choices=["mediation", "path-shift"])
    attribution.add_argument("--config", required=True, help="JSON request matching the attribution schema")
    attribution.add_argument("--output", help="optional JSON output path")

    serve = sub.add_parser("serve", help="run the local REST API and web console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    validate = sub.add_parser("validate-scenario", help="validate a ScenarioSpec JSON file")
    validate.add_argument("path")

    args = parser.parse_args()
    if args.command in {"demo", "run"}:
        try:
            engine, _ = _build_engine(use_llm_investigator=args.llm_investigator)
        except ModelClientError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2)
        if args.command == "demo":
            request = ExperimentRequest(
                scenario_id=args.scenario, max_interventions=args.max_interventions,
                runtime_backend="mock", auto_intervene=not args.no_auto_intervene,
            )
            output = args.output
        else:
            values = _load_request_config(Path(args.config))
            if args.no_auto_intervene:
                values["auto_intervene"] = False
            values.setdefault("runtime_backend", "mock")
            request = ExperimentRequest.model_validate(values)
            output = args.output
        report = engine.run(request)
        destination = _save_report(report, output)
        print(report.model_dump_json(indent=2))
        print(f"Report saved to: {destination}", file=sys.stderr)
    elif args.command == "doctor":
        _, backend = _build_engine()
        manifest = backend.capabilities()
        optional = {name: importlib.util.find_spec(name) is not None for name in ["fractal_bench", "stegopot", "mcp"]}
        print(json.dumps({
            "status": "ok", "version": __version__, "backend": manifest.provider_id,
            "backend_mode": manifest.backend_mode, "scenario_count": len(backend.list_scenarios()),
            "optional_integrations_importable": optional,
            "warden_kernel": warden_status(),
            "note": "The default engine is synthetic; import availability alone does not validate a real experiment.",
        }, ensure_ascii=False, indent=2))
    elif args.command == "kernel":
        try:
            client = WardenKernelClient.from_env()
        except WardenKernelError:
            if args.action == "workflow-status":
                client = None
            else:
                raise
        if args.action == "status":
            print(json.dumps(warden_status(client), ensure_ascii=False, indent=2))
        elif args.action == "doctor":
            print(json.dumps(diagnose_kernel(
                client, contract_id=args.contract_id, probe_id=args.probe_id,
                sae_id=args.sae_id, mode=args.mode,
            ), ensure_ascii=False, indent=2))
        elif args.action == "workflow-status":
            if not args.request_id:
                parser.error("kernel workflow-status requires --request-id")
            data_dir = Path(os.environ.get("AGENTGUARD_DATA_DIR", ".agentguard"))
            store = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
            entry = store.get(args.request_id)
            if entry is None:
                print(json.dumps({"status": "not_found", "request_id": args.request_id}, indent=2))
                raise SystemExit(4)
            print(json.dumps({
                "request_id": entry["request_id"], "status": entry["status"],
                "workflow_status": entry["workflow_status"],
                "statistics": store.execution_statistics(args.request_id),
                "revision": entry["revision"], "created_at": entry["created_at"],
                "updated_at": entry["updated_at"], "context": entry["context"],
                "result": entry["result"], "error_code": entry["error_code"],
                "error_message": entry["error_message"], "events": store.events(args.request_id),
            }, ensure_ascii=False, indent=2))
        elif client is None:
            print(json.dumps({"status": "not_configured", "required_env": "AGENTGUARD_WARDEN_URL"}, indent=2))
            raise SystemExit(2)
        else:
            try:
                if args.action == "capabilities":
                    result = {"kernel": "WARDEN", "items": client.capabilities()}
                elif args.action == "resources":
                    result = {"kernel": "WARDEN", "items": client.resources(args.kind)}
                elif args.action == "contracts":
                    result = {"kernel": "WARDEN", "items": client.contracts()}
                else:
                    if not args.config:
                        parser.error(f"kernel {args.action} requires --config")
                    values = json.loads(Path(args.config).read_text(encoding="utf-8"))
                    if args.plan_only:
                        values["start_run"] = False
                    if args.wait:
                        values["wait_for_completion"] = True
                    if args.timeout is not None:
                        if not 1 <= args.timeout <= 120:
                            parser.error("kernel run --timeout must be between 1 and 120 seconds")
                        values["timeout_seconds"] = args.timeout
                    request = WardenAuditRequest.model_validate(values)
                    data_dir = Path(os.environ.get("AGENTGUARD_DATA_DIR", ".agentguard"))
                    store = ExperienceStore(data_dir / "experiences.sqlite3")
                    workflow_store = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
                    expected = os.environ.get("AGENTGUARD_APPROVAL_TOKEN", "")
                    supplied = request.authorization_ref or ""
                    approved = bool(expected and supplied and hmac.compare_digest(expected, supplied))
                    result = WardenLifecycleService(
                        client, experiences=store, workflows=workflow_store
                    ).execute(request, approval_valid=approved)
                    if args.action == "submit":
                        result["submission_status"] = result["status"]
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except (WardenKernelError, ValueError, OSError, json.JSONDecodeError) as exc:
                print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
                raise SystemExit(2)
    elif args.command == "capabilities":
        _, backend = _build_engine()
        print(backend.capabilities().model_dump_json(indent=2))
    elif args.command == "scenarios":
        _, backend = _build_engine()
        if args.action == "show":
            if not args.scenario_id:
                parser.error("scenarios show requires scenario_id")
            try:
                scenario = backend.get_scenario(args.scenario_id)
            except KeyError as exc:
                print(str(exc), file=sys.stderr); raise SystemExit(4)
            print(scenario.model_dump_json(indent=2))
        else:
            print(json.dumps([s.model_dump(mode="json") for s in backend.list_scenarios()], ensure_ascii=False, indent=2))
    elif args.command == "runs":
        try:
            report = _find_report(args.run_id_or_path)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr); raise SystemExit(4)
        if args.action == "status":
            print(json.dumps({k: report.get(k) for k in ["run_id", "experiment_id", "scenario_id", "status", "backend_mode", "causal_claim_status"]}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == "adapters":
        optional = {name: importlib.util.find_spec(name) is not None for name in ["fractal_bench", "stegopot"]}
        print(json.dumps({"integrations": optional, "validated": False,
                          "note": "This is an importability check only; use docs/INTEGRATION_GUIDE.md for live validation."}, ensure_ascii=False, indent=2))
    elif args.command == "artifacts":
        store = LocalArtifactStore(_data_dir() / "artifacts")
        items = store.list_metadata()
        if args.run_id:
            items = [item for item in items if item.metadata.get("run_id") == args.run_id]
        print(json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False, indent=2))
    elif args.command == "benchmark":
        paths = [item.strip() for item in args.runs.split(",") if item.strip()]
        if len(paths) < 2:
            parser.error("benchmark compare requires at least two run IDs or paths")
        reports = [_find_report(item) for item in paths]
        rows = []
        for report in reports:
            evaluation = report.get("evaluation") or {}
            metrics = evaluation.get("post_intervention_metrics") or {}
            rows.append({"run_id": report.get("run_id"), "scenario_id": report.get("scenario_id"),
                         "backend_mode": report.get("backend_mode"), "status": report.get("status"),
                         "metrics": {name: value.get("value") for name, value in metrics.items()}})
        print(json.dumps({"runs": rows, "warning": "Comparisons are descriptive; ensure matched conditions and independent repeats before causal claims."}, ensure_ascii=False, indent=2))
    elif args.command == "experience":
        store = ExperienceStore(_data_dir() / "experiences.sqlite3")
        if args.action == "stats":
            print(json.dumps(store.action_statistics(verified_only=args.verified_only), ensure_ascii=False, indent=2))
        elif args.action == "recommend":
            records = store.search(scenario_id=args.scenario_id, risk_type=args.risk_type,
                                   verified_only=False, limit=500)
            print(json.dumps(recommend_actions(
                records, scenario_id=args.scenario_id, risk_type=args.risk_type,
                seed=args.seed, limit=args.limit
            ), ensure_ascii=False, indent=2))
        else:
            items = store.search(args.query, scenario_id=args.scenario_id, risk_type=args.risk_type,
                                 verified_only=args.verified_only, limit=100)
            print(json.dumps({"total": len(items), "items": [item.model_dump(mode="json") for item in items],
                              "note": "Unverified experience is advisory only and is never auto-executed."}, ensure_ascii=False, indent=2))
    elif args.command == "attribution":
        payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.action == "mediation":
            request = AttributionNodeAnalysisRequest.model_validate(payload)
            result = analyze_attribution_node(request)
        else:
            request = PathShiftAnalysisRequest.model_validate(payload)
            result = analyze_path_shift(request)
        artifact = LocalArtifactStore(_data_dir() / "artifacts").put(
            result.model_dump_json(indent=2).encode("utf-8"),
            name=f"{args.action}-{result.analysis_id}.json",
            description=f"AgentGuard {args.action} analysis report",
            tags=["attribution", args.action, result.evidence_mode],
            metadata={"analysis_id": result.analysis_id, "evidence_mode": result.evidence_mode},
            media_type="application/json",
        )
        result = result.model_copy(update={"report_artifact_id": artifact.artifact_ref.artifact_id})
        rendered = result.model_dump_json(indent=2)
        if args.output:
            target = Path(args.output).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    elif args.command == "agents":
        configured = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        print(json.dumps({
            "llm_investigator_available": configured,
            "enabled_by_default": False,
            "provider_base_url": os.environ.get("AGENTGUARD_LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")),
            "model": os.environ.get("AGENTGUARD_LLM_MODEL", "deepseek-chat"),
            "security_boundary": "The model may prioritize an existing finding only; it has no tools and cannot authorize or execute interventions.",
            "note": "Configuration check only; this command does not contact the provider.",
        }, ensure_ascii=False, indent=2))
    elif args.command == "study":
        registry = ResearchRegistry(_data_dir() / "research_registry.sqlite3")
        result: Any
        if args.action == "register":
            if not args.config:
                parser.error("study register requires --config")
            request = StudyRegistrationRequest.model_validate_json(Path(args.config).read_text(encoding="utf-8"))
            result = registry.register(request)
        elif args.action == "list":
            items = registry.list()
            result = {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}
        else:
            if not args.study_id:
                parser.error(f"study {args.action} requires --study-id")
            registered = registry.get(args.study_id)
            if registered is None:
                print(f"Unknown study_id: {args.study_id}", file=sys.stderr)
                raise SystemExit(4)
            if args.action == "show":
                result = registered
            elif args.action == "integrity":
                result = {"study_id": args.study_id, "registration_sha256": registered.registration_sha256,
                          "integrity_valid": registry.verify_registration(args.study_id)}
            elif args.action == "analyses":
                items = registry.analyses(args.study_id)
                result = {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}
            else:
                if not args.data:
                    parser.error("study analyze requires --data")
                request = StudyAnalysisRequest.model_validate_json(Path(args.data).read_text(encoding="utf-8"))
                result = analyze_registered_study(registered, request)
                artifact = LocalArtifactStore(_data_dir() / "artifacts").put(
                    result.model_dump_json(indent=2).encode("utf-8"),
                    name=f"study-analysis-{result.analysis_id}.json",
                    description="Preregistered, case-family-aware statistical analysis report",
                    tags=["study-analysis", result.evidence_mode, result.multiplicity_method],
                    metadata={"study_id": registered.study_id, "analysis_id": result.analysis_id,
                              "registration_sha256": registered.registration_sha256,
                              "analysis_sha256": result.analysis_sha256},
                    media_type="application/json",
                )
                result = result.model_copy(update={"report_artifact_id": artifact.artifact_ref.artifact_id})
                registry.add_analysis(result)
        rendered = result.model_dump_json(indent=2) if hasattr(result, "model_dump_json") else json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            target = Path(args.output).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    elif args.command == "serve":
        uvicorn.run("agentguard.api.app:app", host=args.host, port=args.port, reload=False)
    elif args.command == "validate-scenario":
        from agentguard.contracts.scenario import ScenarioSpec
        data = json.loads(Path(args.path).read_text(encoding="utf-8"))
        scenario = ScenarioSpec.model_validate(data)
        print(scenario.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
