from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
from threading import Event

from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.evidence import EvidenceRecord
from agentguard.contracts.intervention import InterventionResult
from agentguard.contracts.lifecycle import RunTimelineEvent, BudgetUsage
from agentguard.contracts.report import RunReport
from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.protocols import ScenarioProvider, RuntimeProvider, EvaluatorProvider
from agentguard.core.detector import RuleBasedDetector
from agentguard.core.planner import InvestigationPlanner, DefensePlanner
from agentguard.core.policy import InterventionPolicyEngine
from agentguard.agents.coordinator import AgentCoordinator
from agentguard.core.investigation import ActiveInvestigator
from agentguard.core.defense import DefensePlannerService
from agentguard.core.graph import InteractionGraphAnalyzer, MultiLayerDetector


class RunCancellationRequested(RuntimeError):
    """Cooperative cancellation observed at a safe orchestration checkpoint."""


class AgentGuardEngine:
    def __init__(
        self,
        scenarios: ScenarioProvider,
        runtime: RuntimeProvider,
        evaluator: EvaluatorProvider,
        detector: RuleBasedDetector | None = None,
        investigator: InvestigationPlanner | None = None,
        defense_planner: DefensePlanner | None = None,
        policy_engine: InterventionPolicyEngine | None = None,
        coordinator: AgentCoordinator | None = None,
        active_investigator: ActiveInvestigator | None = None,
        defense_service: DefensePlannerService | None = None,
        graph_analyzer: InteractionGraphAnalyzer | None = None,
    ) -> None:
        self.scenarios = scenarios
        self.runtime = runtime
        self.evaluator = evaluator
        self.detector = detector or MultiLayerDetector()
        self.investigator = investigator or InvestigationPlanner()
        self.defense_planner = defense_planner or DefensePlanner()
        self.policy_engine = policy_engine or InterventionPolicyEngine()
        self.coordinator = coordinator or AgentCoordinator(
            detector=self.detector,
            investigator=self.investigator,
            defense_planner=self.defense_planner,
        )
        self.active_investigator = active_investigator or ActiveInvestigator()
        self.defense_service = defense_service or DefensePlannerService()
        self.graph_analyzer = graph_analyzer or InteractionGraphAnalyzer()

    def run(self, request: ExperimentRequest, *, cancel_event: Event | None = None) -> RunReport:
        started = monotonic()
        run_id = f"run-{uuid4().hex[:12]}"
        timeline: list[RunTimelineEvent] = []
        coordinator = self.coordinator.new_run()
        scenario = None
        capabilities = None
        observations = []
        graph_analysis = None
        findings = []
        hypotheses = []
        investigation_actions = []
        experiment_designs = []
        intervention_request = None
        intervention_result = None
        intervention_attempts = 0
        policy_decision = None

        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise RunCancellationRequested("Cancellation requested by caller.")

        def event(stage: str, status: str, message: str, **details) -> None:
            timeline.append(
                RunTimelineEvent(
                    sequence=len(timeline),
                    stage=stage,
                    status=status,
                    message=message,
                    timestamp=datetime.now(timezone.utc),
                    details=details,
                )
            )

        coordinator.begin(run_id, request.scenario_id, request.max_interventions)
        event("created", "completed", "Experiment run created.", experiment_id=request.experiment_id)
        event(
            "validated", "completed", "Request schema validation passed.",
            max_interventions=request.max_interventions,
            max_runtime_seconds=request.max_runtime_seconds,
        )

        try:
            check_cancelled()
            scenario = self.scenarios.get_scenario(request.scenario_id)
            event("scenario_loaded", "completed", "Scenario loaded.", scenario_id=scenario.scenario_id)

            check_cancelled()
            capabilities = self.runtime.capabilities()
            if not isinstance(capabilities, CapabilityManifest):
                capabilities = CapabilityManifest.model_validate(capabilities)
            event(
                "validated", "completed", "Runtime capability manifest loaded.",
                provider_id=capabilities.provider_id,
                backend_mode=capabilities.backend_mode,
            )
            if request.runtime_backend not in {"custom", capabilities.provider_id}:
                raise ValueError(
                    f"Requested runtime_backend={request.runtime_backend!r} does not match "
                    f"configured provider_id={capabilities.provider_id!r}."
                )
            if request.policy_profile == "synthetic_demo" and capabilities.backend_mode != "synthetic":
                raise ValueError("policy_profile='synthetic_demo' cannot be used with a real backend")

            check_cancelled()
            observations = self.runtime.execute(scenario, request)
            check_cancelled()
            event(
                "observed", "completed", "Runtime observations collected.",
                observations_count=len(observations),
            )

            graph_analysis = self.graph_analyzer.analyze(observations)
            findings = coordinator.analyze(observations)
            hypotheses = self.active_investigator.generate_hypotheses(request, findings, observations)
            investigation_actions = self.active_investigator.rank_actions(
                hypotheses, findings, capabilities
            )
            if hypotheses and investigation_actions:
                top_hypothesis = next(
                    item for item in hypotheses
                    if item.hypothesis_id == investigation_actions[0].hypothesis_id
                )
                experiment_designs = [self.active_investigator.design_paired_experiment(
                    request, top_hypothesis, investigation_actions[0], repetitions=5
                )]
            check_cancelled()
            event(
                "detected", "completed", "Risk detection completed.",
                findings_count=len(findings),
                detector="multi_layer_baseline",
            )
            preferred_finding_id = None
            preferred_operation = None
            if investigation_actions:
                top_action = investigation_actions[0]
                top_hypothesis = next((item for item in hypotheses if item.hypothesis_id == top_action.hypothesis_id), None)
                if top_hypothesis is not None:
                    preferred_finding_id = top_hypothesis.finding_id
                    preferred_operation = top_action.operation
            selected = coordinator.investigate(
                findings,
                max_interventions=request.max_interventions,
                preferred_finding_id=preferred_finding_id,
            )
            if selected is not None and preferred_finding_id != selected.finding_id:
                # A model may prioritize another existing finding. Do not reuse an action
                # ranked for a different target; let the defense planner choose afresh.
                preferred_operation = None

            if selected is None:
                event("planned", "skipped", "No candidate finding was selected.")
            elif request.policy_profile == "read_only":
                event("planned", "skipped", "Read-only policy profile prevents intervention requests.")
            elif request.max_interventions <= 0:
                event("planned", "skipped", "Intervention budget is zero.")
            elif not request.auto_intervene:
                intervention_request = coordinator.propose_defense(
                    selected, request.experiment_id, f"int-{uuid4().hex[:10]}",
                    operation_override=preferred_operation,
                )
                event(
                    "planned", "completed", "Intervention proposal created; automatic execution is disabled.",
                    intervention_id=intervention_request.intervention_id,
                    target_ref=intervention_request.target_ref,
                    operation=intervention_request.operation,
                )
                event("policy_checked", "skipped", "No write action executed because auto_intervene=false.")
            else:
                intervention_request = coordinator.propose_defense(
                    selected, request.experiment_id, f"int-{uuid4().hex[:10]}",
                    operation_override=preferred_operation,
                )
                event(
                    "planned", "completed", "Candidate intervention proposed.",
                    intervention_id=intervention_request.intervention_id,
                    target_ref=intervention_request.target_ref,
                    operation=intervention_request.operation,
                )
                policy_decision = self.policy_engine.evaluate(
                    scenario,
                    intervention_request,
                    capabilities,
                    max_interventions=request.max_interventions,
                    interventions_already_attempted=intervention_attempts,
                    approval_granted=False,
                )
                coordinator.policy_result(
                    intervention_request, policy_decision.status, policy_decision.reason
                )
                event(
                    "policy_checked",
                    "completed" if policy_decision.allowed else "rejected",
                    policy_decision.reason,
                    decision=policy_decision.status,
                    error_code=policy_decision.error_code or "",
                )

                if policy_decision.allowed:
                    intervention_attempts += 1
                    intervention_result = self.runtime.intervene(intervention_request)
                    coordinator.intervention_result(intervention_result)
                    event(
                        "intervened",
                        "completed" if intervention_result.status == "completed" else "rejected",
                        intervention_result.message,
                        result_status=intervention_result.status,
                        effect_status=intervention_result.effect_status,
                    )
                else:
                    result_status = (
                        "unsupported" if policy_decision.status == "unsupported" else "rejected"
                    )
                    intervention_result = InterventionResult(
                        intervention_id=intervention_request.intervention_id,
                        status=result_status,
                        changed=False,
                        effect_status="not_measured",
                        message=policy_decision.reason,
                        error_code=policy_decision.error_code,
                        metadata={"policy_decision": policy_decision.status},
                    )
                    coordinator.intervention_result(intervention_result)

            check_cancelled()
            evaluation = self.evaluator.evaluate(
                scenario, request, observations, intervention_result
            )
            coordinator.evaluation_result(evaluation)
            event(
                "evaluated",
                "completed" if evaluation.status == "completed" else "failed",
                "Evaluation provider returned a report.",
                evaluation_status=evaluation.status,
                evidence_mode=evaluation.evidence_mode,
            )

            defense_preview = None
            provisional_report = RunReport(
                run_id=run_id,
                experiment_id=request.experiment_id,
                scenario_id=scenario.scenario_id,
                status="completed" if evaluation.status == "completed" else "partial",
                backend_mode=capabilities.backend_mode,
                policy_profile=request.policy_profile,
                request_snapshot=request,
                observations=observations,
                graph_analysis=graph_analysis,
                detector_layers=list(getattr(self.detector, "layer_names", ["rule_based"])),
                findings=findings,
                hypotheses=hypotheses,
                investigation_actions=investigation_actions,
                experiment_designs=experiment_designs,
                selected_intervention=intervention_request,
                intervention_result=intervention_result,
                intervention_requests=[intervention_request] if intervention_request is not None else [],
                intervention_history=[intervention_result] if intervention_result is not None else [],
                evaluation=evaluation,
                capability_manifest=capabilities,
            )
            defense_preview = self.defense_service.preview(provisional_report)

            evidence: list[EvidenceRecord] = []
            for finding in findings:
                evidence.append(
                    EvidenceRecord(
                        kind="finding",
                        grade="synthetic" if capabilities.backend_mode == "synthetic" else "observational",
                        source="agentguard.rule_based_detector",
                        summary=f"{finding.risk_type}: {finding.rationale}",
                        artifact_refs=finding.evidence_refs,
                        metadata={
                            "finding_id": finding.finding_id,
                            "score": finding.score,
                            "target_ref": finding.target_ref,
                        },
                    )
                )
            if policy_decision is not None:
                evidence.append(
                    EvidenceRecord(
                        kind="note",
                        grade="synthetic" if capabilities.backend_mode == "synthetic" else "observational",
                        source="agentguard.intervention_policy",
                        summary=policy_decision.reason,
                        metadata={
                            "status": policy_decision.status,
                            "error_code": policy_decision.error_code or "",
                            "matched_rules": ",".join(policy_decision.matched_rules),
                        },
                    )
                )
            if intervention_result is not None:
                grade = (
                    "synthetic"
                    if intervention_result.effect_status == "simulated"
                    else "interventional"
                )
                evidence.append(
                    EvidenceRecord(
                        kind="intervention",
                        grade=grade,
                        source="runtime_provider_or_policy_gate",
                        summary=intervention_result.message,
                        artifact_refs=intervention_result.evidence_refs,
                        metadata={
                            "status": intervention_result.status,
                            "effect_status": intervention_result.effect_status,
                            "changed": intervention_result.changed,
                        },
                    )
                )
            for observation in observations:
                evidence.append(EvidenceRecord(
                    kind="observation",
                    grade="synthetic" if capabilities.backend_mode == "synthetic" else "observational",
                    source=observation.provenance,
                    summary=observation.summary[:1000],
                    run_id=run_id,
                    experiment_id=request.experiment_id,
                    scenario_id=scenario.scenario_id,
                    source_version=capabilities.provider_version,
                    access_level=observation.visibility,
                    metadata={"event_id": observation.event_id, "event_type": observation.event_type,
                              "target_ref": observation.target_ref or ""},
                ))
            for hypothesis in hypotheses:
                evidence.append(EvidenceRecord(
                    kind="hypothesis",
                    grade="synthetic" if capabilities.backend_mode == "synthetic" else "observational",
                    source="agentguard.active_investigator",
                    summary=hypothesis.statement,
                    artifact_refs=hypothesis.supporting_evidence,
                    run_id=run_id,
                    experiment_id=request.experiment_id,
                    scenario_id=scenario.scenario_id,
                    source_version="heuristic-0.3",
                    metadata={"hypothesis_id": hypothesis.hypothesis_id, "status": hypothesis.status,
                              "confidence": hypothesis.confidence, "next_test": hypothesis.next_test},
                ))
            for design in experiment_designs:
                evidence.append(EvidenceRecord(
                    kind="experiment_design",
                    grade="synthetic" if capabilities.backend_mode == "synthetic" else "observational",
                    source="agentguard.active_investigator",
                    summary="A paired treatment/control design was proposed; no paired execution is implied.",
                    run_id=run_id,
                    experiment_id=request.experiment_id,
                    scenario_id=scenario.scenario_id,
                    source_version="heuristic-0.3",
                    metadata={"design_id": design.design_id, "repetitions": design.repetitions,
                              "preregistered": design.preregistered, "design_type": design.design_type},
                ))
            evidence.append(EvidenceRecord(
                kind="defense",
                grade="synthetic" if capabilities.backend_mode == "synthetic" else "observational",
                source="agentguard.defense_planner",
                summary="Defense candidates were previewed; no effect is claimed from preview estimates.",
                run_id=run_id,
                experiment_id=request.experiment_id,
                scenario_id=scenario.scenario_id,
                source_version="heuristic-0.3",
                metadata={"execution_performed": False, "candidate_count": len(defense_preview.candidates)},
            ))
            evidence.append(
                EvidenceRecord(
                    kind="evaluation",
                    grade="synthetic" if evaluation.evidence_mode == "synthetic" else "interventional",
                    source="evaluator_provider",
                    summary="Evaluation completed; inspect metric evidence_mode before interpretation.",
                    metadata={"status": evaluation.status, "evidence_mode": evaluation.evidence_mode},
                )
            )

            if intervention_result is None:
                causal_status = "not_assessed"
            elif intervention_result.effect_status == "simulated":
                causal_status = "simulated_only"
            elif intervention_result.effect_status == "measured":
                causal_status = "causal_validation_required"
            else:
                causal_status = "not_assessed"

            limitations = [
                "Detection findings are candidate indicators, not proof of malicious intent.",
                "An intervention outcome alone does not establish a causal claim; matched controls and "
                "normal-task utility checks are required.",
                "High-impact defensive actions must be reviewed and executed only in an authorized "
                "isolated environment.",
                "Investigation actions are ranked by transparent heuristics; expected information gain is not calibrated.",
                "Paired experiment designs are plans only; they do not prove that treatment/control runs were executed.",
            ]
            if capabilities.backend_mode == "synthetic":
                limitations.insert(
                    0, "Synthetic backend only: output is for software/interface validation, not empirical evidence."
                )
            elapsed = max(0.0, monotonic() - started)
            runtime_exceeded = elapsed > request.max_runtime_seconds
            usage_method = getattr(self.runtime, "usage_for", None)
            usage = usage_method(request.experiment_id) if callable(usage_method) else {}
            model_calls_used = usage.get("model_calls_used")
            model_tokens_used = usage.get("model_tokens_used")
            tool_calls_used = usage.get("tool_calls_used")
            model_call_exceeded = (
                isinstance(model_calls_used, int)
                and model_calls_used > request.max_model_calls
            )
            model_token_exceeded = (
                isinstance(model_tokens_used, int)
                and model_tokens_used > request.max_model_tokens
            )
            tool_call_exceeded = (
                isinstance(tool_calls_used, int)
                and tool_calls_used > request.max_tool_calls
            )
            status = (
                "completed"
                if evaluation.status == "completed"
                and not runtime_exceeded
                and not model_call_exceeded
                and not model_token_exceeded
                and not tool_call_exceeded
                else "partial"
            )
            if runtime_exceeded:
                limitations.append(
                    "The run exceeded its declared time budget. StegoPot can enforce a cooperative "
                    "deadline, but an in-flight provider call cannot be forcibly terminated by this wrapper."
                )
                event("partial", "info", "Declared runtime budget was exceeded.", elapsed_seconds=elapsed)
            if model_call_exceeded:
                limitations.append("Observed model-call usage exceeded the declared AgentGuard budget.")
            if model_token_exceeded:
                limitations.append(
                    "Observed total token usage exceeded the declared token budget; this is detected from "
                    "provider-reported usage after execution, not a hard pre-call token ceiling."
                )
            if tool_call_exceeded:
                limitations.append("Observed tool-call usage exceeded the declared AgentGuard budget.")
            coordinator.finish(status)
            event(
                "completed" if status == "completed" else "partial",
                "completed",
                "Run report assembled.",
                model_calls_used=model_calls_used if model_calls_used is not None else -1,
                model_tokens_used=model_tokens_used if model_tokens_used is not None else -1,
                tool_calls_used=tool_calls_used if tool_calls_used is not None else -1,
            )

            budget_usage = BudgetUsage(
                max_interventions=request.max_interventions,
                intervention_requests=1 if intervention_request is not None else 0,
                intervention_attempts=intervention_attempts,
                observations_collected=len(observations),
                findings_generated=len(findings),
                elapsed_seconds=round(elapsed, 6),
                max_runtime_seconds=request.max_runtime_seconds,
                runtime_budget_exceeded=runtime_exceeded,
                max_model_calls=request.max_model_calls,
                model_calls_used=model_calls_used,
                max_model_tokens=request.max_model_tokens,
                model_tokens_used=model_tokens_used,
                model_token_budget_exceeded=model_token_exceeded,
                max_tool_calls=request.max_tool_calls,
                tool_calls_used=tool_calls_used,
                tool_call_budget_exceeded=tool_call_exceeded,
            )
            return RunReport(
                run_id=run_id,
                experiment_id=request.experiment_id,
                scenario_id=scenario.scenario_id,
                status=status,
                backend_mode=capabilities.backend_mode,
                policy_profile=request.policy_profile,
                request_snapshot=request,
                observations=observations,
                graph_analysis=graph_analysis,
                detector_layers=list(getattr(self.detector, "layer_names", ["rule_based"])),
                findings=findings,
                hypotheses=hypotheses,
                investigation_actions=investigation_actions,
                experiment_designs=experiment_designs,
                defense_preview=defense_preview,
                selected_intervention=intervention_request,
                intervention_result=intervention_result,
                intervention_requests=[intervention_request] if intervention_request is not None else [],
                intervention_history=[intervention_result] if intervention_result is not None else [],
                evaluation=evaluation,
                evidence=evidence,
                capability_manifest=capabilities,
                timeline=timeline,
                budget_usage=budget_usage,
                agent_messages=list(coordinator.messages),
                causal_claim_status=causal_status,
                limitations=limitations,
            )
        except RunCancellationRequested as exc:
            event("partial", "info", "Run cancelled at an orchestration checkpoint.", reason=str(exc))
            coordinator.finish("partial")
            elapsed = max(0.0, monotonic() - started)
            mode = capabilities.backend_mode if capabilities is not None else getattr(self.runtime, "backend_mode", "synthetic")
            provider_manifest = capabilities
            if provider_manifest is None:
                try:
                    provider_manifest = self.runtime.capabilities()
                except Exception:
                    provider_manifest = None
            usage_method = getattr(self.runtime, "usage_for", None)
            usage = usage_method(request.experiment_id) if callable(usage_method) else {}
            return RunReport(
                run_id=run_id,
                experiment_id=request.experiment_id,
                scenario_id=request.scenario_id,
                status="partial",
                backend_mode=mode,
                policy_profile=request.policy_profile,
                request_snapshot=request,
                observations=observations,
                graph_analysis=graph_analysis,
                detector_layers=list(getattr(self.detector, "layer_names", ["rule_based"])),
                findings=findings,
                hypotheses=hypotheses,
                investigation_actions=investigation_actions,
                experiment_designs=experiment_designs,
                selected_intervention=intervention_request,
                intervention_result=intervention_result,
                intervention_requests=[intervention_request] if intervention_request is not None else [],
                intervention_history=[intervention_result] if intervention_result is not None else [],
                capability_manifest=provider_manifest,
                timeline=timeline,
                budget_usage=BudgetUsage(
                    max_interventions=request.max_interventions,
                    intervention_requests=1 if intervention_request else 0,
                    intervention_attempts=intervention_attempts,
                    observations_collected=len(observations),
                    findings_generated=len(findings),
                    elapsed_seconds=round(elapsed, 6),
                    max_runtime_seconds=request.max_runtime_seconds,
                    runtime_budget_exceeded=elapsed > request.max_runtime_seconds,
                    max_model_calls=request.max_model_calls,
                    model_calls_used=usage.get("model_calls_used"),
                    max_model_tokens=request.max_model_tokens,
                    model_tokens_used=usage.get("model_tokens_used"),
                    max_tool_calls=request.max_tool_calls,
                    tool_calls_used=usage.get("tool_calls_used"),
                ),
                agent_messages=list(coordinator.messages),
                causal_claim_status="not_assessed",
                limitations=[
                    "Cancellation is cooperative at orchestration checkpoints; a synchronous backend call already in flight cannot be forcibly interrupted.",
                    "This partial report contains only evidence collected before cancellation was observed.",
                ],
            )
        except Exception as exc:
            event("failed", "failed", "Run failed before a complete report could be assembled.", error=str(exc))
            raise
