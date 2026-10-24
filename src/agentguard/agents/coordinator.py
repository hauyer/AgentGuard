"""Role-separated investigation coordination with a deterministic baseline.

The roles are bounded software components, not autonomous LLM agents. The optional
LLM advisor receives candidate summaries and has no tools or execution authority.
"""

from __future__ import annotations

from agentguard.agents.investigation_strategy import InvestigationStrategy, rank_findings
from agentguard.agents.role_permissions import (
    ROLE_TOOL_ALLOWLIST,
    authorize_role_message,
    authorize_role_tool,
)
from agentguard.contracts.agent_message import AgentMessage
from agentguard.contracts.evaluation import EvaluationReport
from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.hypothesis import InvestigationAction, InvestigationHypothesis
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.observation import DetectionFinding, Observation
from agentguard.core.planner import DefensePlanner, InvestigationPlanner


class AgentCoordinator:
    def __init__(
        self,
        detector=None,
        investigator: InvestigationPlanner | None = None,
        defense_planner: DefensePlanner | None = None,
        advisor=None,
    ) -> None:
        self.detector = detector
        if self.detector is None:
            from agentguard.core.detector import RuleBasedDetector

            self.detector = RuleBasedDetector()
        self.investigator = investigator or InvestigationPlanner()
        self.defense_planner = defense_planner or DefensePlanner()
        self.advisor = advisor
        self.messages: list[AgentMessage] = []
        self.run_id = ""
        self.agent_steps_used = 0
        self.model_calls_used = 0
        self.model_tokens_used: int | None = 0

    def new_run(self) -> AgentCoordinator:
        """Return isolated run state while preserving immutable configured services."""
        return AgentCoordinator(
            detector=self.detector,
            investigator=self.investigator,
            defense_planner=self.defense_planner,
            advisor=self.advisor.new_run() if self.advisor is not None and hasattr(self.advisor, "new_run") else self.advisor,
        )

    @staticmethod
    def authorize_tool(role: str, tool_name: str) -> None:
        authorize_role_tool(role, tool_name)

    def _send(
        self,
        sender: str,
        recipient: str,
        message_type: str,
        payload: dict,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> None:
        authorize_role_message(sender, recipient, message_type)
        self.messages.append(
            AgentMessage(
                run_id=self.run_id,
                sender_role=sender,
                recipient_role=recipient,
                message_type=message_type,
                payload=payload,
                evidence_refs=evidence_refs or [],
                confidence=confidence,
            )
        )

    def begin(self, run_id: str, scenario_id: str, observation_budget: int) -> None:
        self.run_id = run_id
        self.messages = []
        self.agent_steps_used = 0
        self.model_calls_used = 0
        self.model_tokens_used = 0
        self.authorize_tool("supervisor", "delegate_trace_analysis")
        self._send(
            "supervisor",
            "trace_analyst",
            "analyze_observations",
            {"scenario_id": scenario_id, "observation_budget": observation_budget},
        )

    def analyze(self, observations: list[Observation]) -> list[DetectionFinding]:
        self.authorize_tool("trace_analyst", "analyze_observations")
        findings = self.detector.detect(observations)
        refs = sorted({ref for finding in findings for ref in finding.evidence_refs})
        self._send(
            "trace_analyst",
            "supervisor",
            "findings_ready",
            {"observations_count": len(observations), "findings_count": len(findings)},
            evidence_refs=refs,
            confidence=max((item.score for item in findings), default=None),
        )
        return findings

    def investigate(
        self,
        findings: list[DetectionFinding],
        *,
        max_interventions: int,
        strategy: InvestigationStrategy | None = None,
        actions: list[InvestigationAction] | None = None,
        hypotheses: list[InvestigationHypothesis] | None = None,
        experience_records: list[ExperienceRecord] | None = None,
        scenario_id: str | None = None,
        max_agent_steps: int = 32,
        max_model_calls_remaining: int | None = 1,
        max_model_tokens_remaining: int | None = 384,
        max_output_tokens_per_call: int = 384,
        preferred_finding_id: str | None = None,
    ) -> DetectionFinding | None:
        self.authorize_tool("causal_investigator", "rank_candidates")
        chosen_strategy: InvestigationStrategy = strategy or (
            "llm" if self.advisor is not None else "information_value"
        )
        if chosen_strategy == "experience":
            self.authorize_tool("memory_agent", "rank_verified_experiences")
        self._send(
            "supervisor",
            "causal_investigator",
            "select_next_investigation",
            {
                "candidate_count": len(findings),
                "remaining_interventions": max_interventions,
            },
        )

        ranked, fallback_reason = rank_findings(
            findings,
            strategy=chosen_strategy,
            actions=actions,
            hypotheses=hypotheses,
            experience_records=experience_records,
            scenario_id=scenario_id,
            max_candidates=max_agent_steps,
        )
        self.agent_steps_used = len(ranked)
        self._send(
            "causal_investigator",
            "supervisor",
            "strategy_started",
            {
                "strategy": chosen_strategy,
                "candidate_count": len(ranked),
                "truncated_count": max(0, len(findings) - len(ranked)),
                "max_agent_steps": max_agent_steps,
                "fallback_reason": fallback_reason or "",
            },
        )
        for item in ranked:
            self._send(
                "causal_investigator",
                "supervisor",
                "candidate_ranked",
                {
                    "strategy": chosen_strategy,
                    "finding_id": item.finding.finding_id,
                    "rank": item.rank,
                    "score": round(item.score, 8),
                    "rationale": item.rationale[:300],
                    "eligible_experience_count": item.eligible_experience_count,
                },
                evidence_refs=item.finding.evidence_refs,
                confidence=max(0.0, min(1.0, item.finding.score)),
            )

        selected = ranked[0].finding if ranked else None
        llm_rationale = ""
        llm_status = "not_requested"
        llm_fallback = fallback_reason or ""
        should_call_llm = chosen_strategy == "llm"
        if should_call_llm:
            allowed_findings = [item.finding for item in ranked]
            decision = getattr(self.advisor, "last_decision", {}) if self.advisor is not None else {}
            if self.advisor is None:
                llm_status = "advisor_unavailable_fallback"
                llm_fallback = "advisor_unavailable_fallback_to_information_value"
            elif max_model_calls_remaining is None or max_model_tokens_remaining is None:
                llm_status = "model_budget_unknown_fallback"
                llm_fallback = "model_budget_unknown_fallback_to_information_value"
            elif max_model_calls_remaining < 1 or max_model_tokens_remaining < 1:
                llm_status = "model_budget_exhausted_fallback"
                llm_fallback = "model_budget_exhausted_fallback_to_information_value"
            elif not allowed_findings:
                llm_status = "no_candidates"
            else:
                output_tokens = min(384, max_output_tokens_per_call, max_model_tokens_remaining)
                advised_id = self.advisor.select_finding(
                    allowed_findings,
                    max_tokens=output_tokens,
                    max_candidates=min(25, max_agent_steps),
                )
                decision = getattr(self.advisor, "last_decision", {})
                llm_status = str(decision.get("status", "unknown"))
                llm_rationale = str(decision.get("rationale", decision.get("error", "")))[:300]
                model_calls = decision.get("model_calls_used", 0)
                if isinstance(model_calls, int) and model_calls > 0:
                    self.model_calls_used += model_calls
                model_tokens = decision.get("model_tokens_used")
                if isinstance(model_tokens, int) and model_tokens >= 0:
                    if self.model_tokens_used is not None:
                        self.model_tokens_used += model_tokens
                elif model_calls:
                    self.model_tokens_used = None
                candidate_ids = {item.finding.finding_id for item in ranked[:25]}
                if advised_id is not None and advised_id in candidate_ids:
                    selected = next(
                        item.finding for item in ranked if item.finding.finding_id == advised_id
                    )
                    llm_fallback = ""
                elif llm_status != "advice_received":
                    llm_fallback = f"{llm_status}_fallback_to_information_value"
                else:
                    llm_fallback = "model_selected_no_candidate_fallback_to_information_value"
            model_calls = decision.get("model_calls_used", 0)
            model_tokens = decision.get("model_tokens_used")
            self._send(
                "causal_investigator",
                "supervisor",
                "llm_investigation_advice",
                {
                    "status": llm_status,
                    "provider": str(decision.get("provider", "unknown"))[:80],
                    "model": str(decision.get("model", "unknown"))[:120],
                    "selected_finding_id": str(decision.get("selected_finding_id") or ""),
                    "rationale": llm_rationale,
                    "confidence": float(decision.get("confidence", 0.0))
                    if isinstance(decision.get("confidence", 0.0), (int, float))
                    else 0.0,
                    "model_calls_used": model_calls if isinstance(model_calls, int) else 0,
                    "model_tokens_used": model_tokens if isinstance(model_tokens, int) else -1,
                    "fallback_reason": llm_fallback,
                },
                evidence_refs=sorted({ref for finding in allowed_findings for ref in finding.evidence_refs})[:20],
            )

        if strategy is None and self.advisor is None and preferred_finding_id is not None:
            selected = next(
                (item.finding for item in ranked if item.finding.finding_id == preferred_finding_id),
                selected,
            )

        if selected is None:
            self._send(
                "causal_investigator",
                "supervisor",
                "no_candidate_selected",
                {"reason": "no_findings" if not findings else "candidate_budget_empty"},
            )
        else:
            ranking = next(
                item for item in ranked if item.finding.finding_id == selected.finding_id
            )
            reason = ranking.rationale
            if chosen_strategy == "llm" and llm_status == "advice_received" and not llm_fallback:
                reason = f"LLM advice selected this in-set candidate; advisory rationale: {llm_rationale[:180]}"
            elif llm_fallback:
                reason = f"{reason} Fallback: {llm_fallback}."
            self._send(
                "causal_investigator",
                "supervisor",
                "candidate_selected",
                {
                    "finding_id": selected.finding_id,
                    "risk_type": selected.risk_type,
                    "target_ref": selected.target_ref,
                    "score": selected.score,
                    "strategy": chosen_strategy,
                    "reason": reason[:300],
                },
                evidence_refs=selected.evidence_refs,
                confidence=selected.score,
            )
        return selected

    def propose_defense(
        self,
        finding: DetectionFinding,
        experiment_id: str,
        intervention_id: str,
        operation_override: str | None = None,
    ) -> InterventionRequest:
        self.authorize_tool("defense_planner", "propose_defense")
        self._send(
            "causal_investigator",
            "defense_planner",
            "propose_targeted_defense",
            {
                "risk_type": finding.risk_type,
                "target_type": finding.target_type,
                "target_ref": finding.target_ref,
            },
            evidence_refs=finding.evidence_refs,
            confidence=finding.score,
        )
        request = self.defense_planner.propose(finding, experiment_id, intervention_id)
        if operation_override is not None:
            request.operation = operation_override
        self._send(
            "defense_planner",
            "supervisor",
            "defense_proposed",
            {
                "intervention_id": request.intervention_id,
                "target_type": request.target_type,
                "target_ref": request.target_ref,
                "operation": request.operation,
            },
            evidence_refs=finding.evidence_refs,
            confidence=finding.score,
        )
        return request

    def policy_result(self, request: InterventionRequest, decision_status: str, reason: str) -> None:
        self.authorize_tool("policy_engine", "evaluate_action")
        self._send(
            "policy_engine",
            "supervisor",
            "policy_decision",
            {
                "intervention_id": request.intervention_id,
                "decision": decision_status,
                "reason": reason,
            },
        )

    def intervention_result(self, result: InterventionResult) -> None:
        self._send(
            "runtime",
            "verifier",
            "intervention_result",
            {
                "intervention_id": result.intervention_id,
                "status": result.status,
                "effect_status": result.effect_status,
                "changed": result.changed,
            },
            evidence_refs=result.evidence_refs,
        )

    def evaluation_result(self, evaluation: EvaluationReport) -> None:
        self.authorize_tool("evaluator", "evaluate_run")
        self.authorize_tool("verifier", "summarize_verification")
        self._send(
            "evaluator",
            "verifier",
            "evaluation_result",
            {
                "status": evaluation.status,
                "evidence_mode": evaluation.evidence_mode,
                "metrics_count": len(evaluation.post_intervention_metrics),
            },
        )
        self._send(
            "verifier",
            "supervisor",
            "verification_summary",
            {
                "verification_status": (
                    "synthetic_only"
                    if evaluation.evidence_mode == "synthetic"
                    else "evaluation_recorded"
                ),
                "causal_claim_allowed": False,
            },
        )

    def finish(self, status: str) -> None:
        self.authorize_tool("supervisor", "finish_run")
        self._send(
            "supervisor",
            "supervisor",
            "run_finished",
            {"status": status, "message_count": len(self.messages) + 1},
        )


__all__ = ["ROLE_TOOL_ALLOWLIST", "AgentCoordinator"]
