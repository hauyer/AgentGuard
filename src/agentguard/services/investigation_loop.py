"""Evidence-driven multi-round investigation loop (D3).

The loop composes existing services — interaction graph, evidence gateway,
risk findings, experiment index, experience store and the standardized
observation workflow — into a recorded, budgeted, stoppable investigation.
Actions that actually run are reads and (when the kernel supports it) new
observation audits; intervention planning is recommendation-only and never
recorded as an executed intervention. Every round is a durable decision
record; the report is persisted as a content-addressed artifact.
"""
from __future__ import annotations

import time
from typing import Any

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.investigation_loop import (
    InvestigationLoopBudget,
    InvestigationLoopMetrics,
    InvestigationLoopReport,
    LoopAction,
    LoopDecisionRecord,
    LoopPhase,
)
from agentguard.contracts.run_config import InvestigationRunConfig
from agentguard.memory.store import ExperienceStore
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.experiment_index import ExperimentIndexService
from agentguard.services.interaction_graph import InteractionGraphService
from agentguard.services.investigation_strategies import select_next_action
from agentguard.services.observation_workflow import ObservationRunService
from agentguard.services.risk_findings import RiskFindingService
from agentguard.services.warden_workflow_store import WardenWorkflowStore


class InvestigationLoopError(ValueError):
    """The base investigation cannot host a loop."""


class InvestigationLoopService:
    """Run bounded multi-round investigations over one recorded workflow."""

    def __init__(
        self,
        *,
        workflows: WardenWorkflowStore,
        artifacts: LocalArtifactStore,
        evidence_gateway: EvidenceGateway,
        experiment_index: ExperimentIndexService,
        risk_findings: RiskFindingService,
        interaction_graph: InteractionGraphService | None = None,
        experiences: ExperienceStore | None = None,
        observation: ObservationRunService | None = None,
        llm_client=None,
        monotonic=time.monotonic,
    ) -> None:
        self.workflows = workflows
        self.artifacts = artifacts
        self.evidence_gateway = evidence_gateway
        self.experiment_index = experiment_index
        self.risk_findings = risk_findings
        self.interaction_graph = interaction_graph or InteractionGraphService()
        self.experiences = experiences
        self.observation = observation
        self.llm_client = llm_client
        self._monotonic = monotonic

    # ------------------------------------------------------------------ #
    # public entry
    # ------------------------------------------------------------------ #
    def run(
        self,
        base_request_id: str,
        *,
        strategy: str = "information_value",
        budget: InvestigationLoopBudget | None = None,
        stop_after_rounds: int | None = None,
        registration_id: str | None = None,
    ) -> InvestigationLoopReport:
        entry = self.workflows.get(base_request_id)
        if entry is None:
            raise InvestigationLoopError(f"unknown investigation: {base_request_id}")
        budget = budget or InvestigationLoopBudget()
        started = self._monotonic()

        state = {
            "phase": "CREATED",
            "taken_types": set[str](),
            "evidence_signature": None,
            "discovered_refs": set[str](),
            "dynamic_evidence_refs": set[str](),
            "steps_to_first_evidence": None,
            "redundant": 0,
            "model_calls": 0,
            "audit_ids": [],
            "no_new_streak": 0,
            "degraded": False,
            "degradation_note": "",
            "limitations": [
                (
                    "The wall-clock budget is checked before each action. Model calls "
                    "and WARDEN polling use the remaining time when supported; "
                    "synchronous preflight and local processing may add overhead."
                ),
            ],
        }
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
        source_kind = metadata.get("source_kind")
        if source_kind in {"synthetic", "stub"}:
            state["limitations"].append(
                f"Base investigation declares source_kind={source_kind}; this loop "
                "does not establish a real model observation or real evidence."
            )
        decisions: list[LoopDecisionRecord] = []
        stop_reason = "no_executable_actions"

        while len(decisions) < budget.max_actions:
            if stop_after_rounds is not None and len(decisions) >= stop_after_rounds:
                stop_reason = "manual_stop"
                break
            elapsed = self._monotonic() - started
            if elapsed >= budget.max_seconds:
                stop_reason = "time_budget_reached"
                break
            if strategy == "llm" and state["model_calls"] >= budget.max_model_calls:
                stop_reason = "model_call_budget_reached"
                break

            summary = self._evidence_summary(base_request_id)
            summary["new_evidence_refs"] = sorted(state["dynamic_evidence_refs"])[:32]
            actions = self._available_actions(entry, summary, state)
            executable = [a for a in actions if a.action_type not in state["taken_types"]]
            if not executable and not actions:
                stop_reason = "no_executable_actions"
                break

            remaining = {
                "actions": budget.max_actions - len(decisions),
                "seconds": max(0.0, budget.max_seconds - (self._monotonic() - started)),
                "model_calls": budget.max_model_calls - state["model_calls"],
            }
            if remaining["seconds"] <= 0.0:
                stop_reason = "time_budget_reached"
                break
            state["phase"] = "ACTIONS_RANKED"
            selection = select_next_action(
                strategy,
                taken_types=state["taken_types"],
                actions=actions,
                evidence_summary=summary,
                remaining_budget=remaining,
                llm_client=self.llm_client,
            )
            if selection.degraded:
                state["degraded"] = True
                state["degradation_note"] = selection.degradation_note
            if strategy == "llm" and selection.model_call_attempted:
                state["model_calls"] += 1
            chosen = next(a for a in actions if a.action_id == selection.action_id)

            # Approval gate: reads and budgeted audits pass policy here.
            state["phase"] = "ACTION_APPROVED"
            round_started = self._monotonic()
            remaining_seconds = max(
                0.0, budget.max_seconds - (round_started - started),
            )
            if remaining_seconds <= 0.0:
                stop_reason = "time_budget_reached"
                break
            if chosen.action_type == "submit_observation_audit" and remaining_seconds < 1.0:
                # WARDEN's public request schema has a one-second minimum. Do not
                # round up and exceed the Loop's remaining budget to force a POST.
                stop_reason = "time_budget_reached"
                break
            result_status, evidence_delta, notes = self._execute_action(
                chosen, entry, base_request_id, len(decisions) + 1, state, budget,
                remaining_seconds=remaining_seconds,
            )
            if chosen.action_type == "submit_observation_audit" and result_status == "completed":
                state["dynamic_evidence_refs"].update(evidence_delta)
            actual_cost = self._monotonic() - round_started
            state["phase"] = "ACTION_EXECUTED"

            signature = self._evidence_signature(base_request_id)
            new_refs = [ref for ref in evidence_delta
                        if ref not in state["discovered_refs"]]
            state["discovered_refs"].update(evidence_delta)
            world_changed = signature != state["evidence_signature"]
            if state["evidence_signature"] is None:
                state["evidence_signature"] = signature
                world_changed = False
            new_evidence = bool(new_refs) or world_changed
            redundant = chosen.action_type in state["taken_types"] and not new_evidence
            if redundant:
                state["redundant"] += 1
            state["taken_types"].add(chosen.action_type)
            if new_evidence:
                state["phase"] = "EVIDENCE_UPDATED"
                state["no_new_streak"] = 0
                if state["steps_to_first_evidence"] is None:
                    state["steps_to_first_evidence"] = len(decisions) + 1
            else:
                state["no_new_streak"] += 1
            state["limitations"].extend(notes)

            decisions.append(LoopDecisionRecord(
                round_index=len(decisions) + 1,
                phase=state["phase"],
                selected_action=chosen.action_id,
                candidate_actions=[action.action_id for action in actions],
                selection_strategy=strategy,  # type: ignore[arg-type]
                selection_reason=selection.reason,
                evidence_refs=list(evidence_delta)[:32],
                cost_estimate=chosen.cost_estimate,
                actual_cost=round(actual_cost, 3),
                result_status=result_status,
                new_evidence=new_evidence,
            ))

            if result_status == "blocked" and chosen.requires == "audit":
                stop_reason = "capability_unavailable"
                break
            if state["no_new_streak"] >= budget.no_new_evidence_rounds \
                    and chosen.requires == "read" \
                    and not [a for a in actions if a.action_type not in state["taken_types"]]:
                stop_reason = "no_new_evidence"
                break
        else:
            stop_reason = "action_budget_reached"

        total_elapsed = self._monotonic() - started
        executed = [d for d in decisions if d.result_status == "completed"]
        metrics = InvestigationLoopMetrics(
            rounds_executed=len(decisions),
            steps_to_first_evidence=state["steps_to_first_evidence"],
            effective_action_ratio=(
                len(executed) / len(decisions) if decisions else None
            ),
            blocked_ratio=(
                sum(d.result_status == "blocked" for d in decisions) / len(decisions)
                if decisions else None
            ),
            unsupported_ratio=(
                sum(d.result_status == "unsupported" for d in decisions) / len(decisions)
                if decisions else None
            ),
            redundant_action_count=state["redundant"],
            hypotheses_raised=len(self._hypothesis_ids(base_request_id)),
            evidence_refs_collected=len(state["discovered_refs"]),
            model_calls_used=state["model_calls"],
            elapsed_seconds=round(total_elapsed, 3),
        )
        report = InvestigationLoopReport(
            investigation_id=f"loop-{base_request_id}",
            base_request_id=base_request_id,
            strategy=strategy,  # type: ignore[arg-type]
            strategy_degraded=state["degraded"],
            strategy_degradation_note=state["degradation_note"],
            phase=stop_reason_to_phase(stop_reason, decisions),
            stop_reason=stop_reason,
            decisions=decisions,
            budget=budget,
            metrics=metrics,
            created_audit_request_ids=list(state["audit_ids"]),
            limitations=list(dict.fromkeys(state["limitations"])),
        )
        return self._persist(report, registration_id=registration_id)

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def _available_actions(
        self, entry: dict[str, Any], summary: dict[str, Any], state: dict[str, Any],
    ) -> list[LoopAction]:
        actions = [
            LoopAction(action_id="act-read-state", action_type="read_state",
                       description="Read the durable investigation state",
                       evidence_need="workflow status", cost_estimate=0.1),
            LoopAction(action_id="act-read-graph", action_type="read_graph",
                       description="Project the recorded message-level interaction graph",
                       evidence_need="message edges", cost_estimate=0.2),
            LoopAction(action_id="act-read-evidence", action_type="read_evidence",
                       description="Read the governed evidence summary",
                       evidence_need="evidence items", cost_estimate=0.2),
            LoopAction(action_id="act-read-hypotheses", action_type="read_hypotheses",
                       description="Analyze metadata findings and hypotheses",
                       evidence_need="candidate risks", cost_estimate=0.3),
            LoopAction(action_id="act-read-experiments", action_type="read_experiments",
                       description="Query the experiment index for this scenario",
                       evidence_need="historical runs", cost_estimate=0.2),
        ]
        if self.experiences is not None:
            actions.append(LoopAction(
                action_id="act-read-experiences", action_type="read_experiences",
                description="Search verified historical experiences",
                evidence_need="past verified actions", cost_estimate=0.2,
            ))
        if self.observation is not None and summary.get("kernel_configured"):
            actions.append(LoopAction(
                action_id="act-submit-audit", action_type="submit_observation_audit",
                requires="audit",
                description="Submit a fresh standardized observation audit",
                evidence_need="new real run", cost_estimate=30.0,
            ))
        if summary.get("hypotheses"):
            actions.append(LoopAction(
                action_id="act-plan-intervention", action_type="plan_intervention",
                requires="intervention",
                description="Draft an intervention plan for raised hypotheses (plan-only)",
                evidence_need="intervention design", cost_estimate=0.3,
            ))
        return actions

    def _execute_action(
        self,
        action: LoopAction,
        entry: dict[str, Any],
        base_request_id: str,
        round_index: int,
        state: dict[str, Any],
        budget: InvestigationLoopBudget,
        *,
        remaining_seconds: float,
    ) -> tuple[str, list[str], list[str]]:
        """Execute one action through the real services; returns (status, delta, notes)."""
        if action.action_type == "read_state":
            current = self.workflows.get(base_request_id) or {}
            refs = [base_request_id, str(current.get("workflow_status"))]
            return "completed", refs, []
        if action.action_type == "read_graph":
            graph = self.interaction_graph.for_warden_workflow(entry)
            refs = [edge.id for edge in graph.edges][:32]
            return "completed", refs, []
        if action.action_type == "read_evidence":
            governed = self.evidence_gateway.for_warden_workflow(
                entry, artifacts=self.artifacts,
            )
            refs = [item["evidence_id"] for item in governed.get("items", [])][:32]
            refs.extend(sorted(state["dynamic_evidence_refs"])[:32])
            return "completed", refs, []
        if action.action_type == "read_hypotheses":
            report = self.risk_findings.analyze_workflow(entry)
            refs = [f.finding_id for f in report.findings][:32]
            return "completed", refs, []
        if action.action_type == "read_experiments":
            detail = self.experiment_index.get_experiment(base_request_id)
            refs = [detail.run_id or base_request_id] if detail else []
            return "completed", refs, []
        if action.action_type == "read_experiences":
            assert self.experiences is not None
            request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
            results = self.experiences.search(
                scenario_id=request.get("scenario_id"), verified_only=True, limit=5,
            )
            return "completed", [r.experience_id for r in results][:32], []
        if action.action_type == "submit_observation_audit":
            return self._submit_audit(
                entry, base_request_id, round_index, state, budget,
                remaining_seconds=remaining_seconds,
            )
        if action.action_type == "plan_intervention":
            hypothesis_ids = self._hypothesis_ids(base_request_id)[:8]
            note = (
                "Intervention plan drafted only: no executor is declared, so no "
                "intervention was submitted or executed (DEP-007)."
            )
            return "completed", hypothesis_ids, [note]
        return "skipped", [], ["unknown action type; skipped"]

    def _submit_audit(
        self, entry: dict[str, Any], base_request_id: str, round_index: int,
        state: dict[str, Any], budget: InvestigationLoopBudget,
        *, remaining_seconds: float,
    ) -> tuple[str, list[str], list[str]]:
        assert self.observation is not None
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        config = InvestigationRunConfig(
            run_config_id=f"{base_request_id}-round-{round_index}",
            scenario_id=request.get("scenario_id"),
            contract_id=str(request.get("contract_id") or ""),
            model_id=str(request.get("model_id") or ""),
            runtime_id=str(request.get("runtime_id") or ""),
            audit_mode="observation",
            time_budget=min(60.0, remaining_seconds),
        )
        request_id = f"{base_request_id}-audit-r{round_index}"
        record = self.observation.execute(config, request_id=request_id)
        state["audit_ids"].append(request_id)
        if record.execution_status == "completed":
            if not record.run_id:
                return "blocked", [], [
                    (
                        "WARDEN_OPERATION_ID_UNAVAILABLE: WARDEN reported completion "
                        "without a queryable run ID; the audit was not accepted as a result."
                    )
                ]
            if not record.evidence_refs:
                return "blocked", [], [
                    (
                        "WARDEN_RESULT_EVIDENCE_MISSING: WARDEN reported completion "
                        "without evidence references; the loop remains blocked."
                    )
                ]
            return "completed", list(record.evidence_refs)[:32], []
        if record.execution_status == "blocked":
            return "blocked", [], [
                f"audit blocked: {', '.join(record.blockers[:4])}",
            ]
        return "failed", [], [f"audit ended {record.execution_status}"]

    # ------------------------------------------------------------------ #
    # summaries
    # ------------------------------------------------------------------ #
    def _evidence_summary(self, base_request_id: str) -> dict[str, Any]:
        entry = self.workflows.get(base_request_id) or {}
        graph = self.interaction_graph.for_warden_workflow(entry)
        governed = self.evidence_gateway.for_warden_workflow(
            entry, artifacts=self.artifacts,
        )
        risk = self.risk_findings.analyze_workflow(entry)
        scenario_id = (entry.get("request") or {}).get("scenario_id") \
            if isinstance(entry.get("request"), dict) else None
        experiences = 0
        if self.experiences is not None:
            experiences = len(self.experiences.search(
                scenario_id=scenario_id, verified_only=True, limit=5,
            ))
        experiments = [
            item for item in self.experiment_index.list_experiments(limit=500).items
            if item.scenario_id == scenario_id
        ] if scenario_id else []
        return {
            "graph_available": graph.available,
            "graph_edges": len(graph.edges),
            "evidence_items": len(governed.get("items", [])),
            "findings": len(risk.findings),
            "hypotheses": len(risk.hypotheses),
            "experiences": experiences,
            "experiment_entries": len(experiments),
            "kernel_configured": self.observation is not None
            and self._kernel_configured(),
        }

    def _kernel_configured(self) -> bool:
        assert self.observation is not None
        try:
            kernel = self.observation._kernel()
            kernel.health()
            return True
        except Exception:  # noqa: BLE001 - unconfigured/unreachable both disable audits
            return False

    def _evidence_signature(self, base_request_id: str) -> tuple:
        summary = self._evidence_summary(base_request_id)
        risk = self.risk_findings.analyze_workflow(
            self.workflows.get(base_request_id) or {},
        )
        return (
            summary["graph_available"], summary["graph_edges"],
            summary["evidence_items"], len(risk.findings), len(risk.hypotheses),
        )

    def _hypothesis_ids(self, base_request_id: str) -> list[str]:
        entry = self.workflows.get(base_request_id) or {}
        risk = self.risk_findings.analyze_workflow(entry)
        return [item.hypothesis_id for item in risk.hypotheses]

    def _persist(
        self, report: InvestigationLoopReport, *, registration_id: str | None = None,
    ) -> InvestigationLoopReport:
        report.registration_id = registration_id
        artifact = self.artifacts.put(
            report.model_dump_json(indent=2).encode("utf-8"),
            name=f"investigation-loop-{report.base_request_id}-{report.strategy}.json",
            description="Multi-round investigation loop report",
            tags=["investigation-loop", report.strategy, report.stop_reason],
            metadata={
                "base_request_id": report.base_request_id,
                "strategy": report.strategy,
                "rounds": report.metrics.rounds_executed,
                "stop_reason": report.stop_reason,
                "registration_id": registration_id or "unregistered",
            },
            media_type="application/json",
        )
        report.report_artifact_id = artifact.artifact_ref.artifact_id
        return report


def stop_reason_to_phase(stop_reason: str, decisions: list[LoopDecisionRecord]) -> LoopPhase:
    if stop_reason in {"action_budget_reached", "time_budget_reached",
                       "model_call_budget_reached", "manual_stop", "no_new_evidence"}:
        return "STOPPED"
    if stop_reason in {"no_executable_actions", "capability_unavailable"}:
        return "BLOCKED"
    if decisions and all(decision.result_status == "completed" for decision in decisions):
        return "COMPLETED"
    return "STOPPED"
