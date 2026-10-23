"""Role-separated deterministic multi-agent coordination baseline.

These are explicit software roles, not autonomous LLM agents. They create a typed
coordination trace that can later be replaced or augmented by LLM-backed workers.
"""

from __future__ import annotations

from agentguard.contracts.agent_message import AgentMessage
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.observation import DetectionFinding, Observation
from agentguard.contracts.evaluation import EvaluationReport
from agentguard.core.detector import RuleBasedDetector
from agentguard.core.planner import InvestigationPlanner, DefensePlanner


class AgentCoordinator:
    def __init__(
        self,
        detector: RuleBasedDetector | None = None,
        investigator: InvestigationPlanner | None = None,
        defense_planner: DefensePlanner | None = None,
        advisor=None,
    ) -> None:
        self.detector = detector or RuleBasedDetector()
        self.investigator = investigator or InvestigationPlanner()
        self.defense_planner = defense_planner or DefensePlanner()
        self.advisor = advisor
        self.messages: list[AgentMessage] = []
        self.run_id = ""

    def new_run(self) -> "AgentCoordinator":
        """Return an isolated coordinator instance for one run/thread."""
        return AgentCoordinator(
            detector=self.detector,
            investigator=self.investigator,
            defense_planner=self.defense_planner,
            advisor=self.advisor.new_run() if self.advisor is not None and hasattr(self.advisor, "new_run") else self.advisor,
        )

    def _send(
        self,
        sender: str,
        recipient: str,
        message_type: str,
        payload: dict,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> None:
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
        self._send(
            "supervisor",
            "trace_analyst",
            "analyze_observations",
            {"scenario_id": scenario_id, "observation_budget": observation_budget},
        )

    def analyze(self, observations: list[Observation]) -> list[DetectionFinding]:
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
        preferred_finding_id: str | None = None,
    ) -> DetectionFinding | None:
        self._send(
            "supervisor",
            "causal_investigator",
            "select_next_investigation",
            {"candidate_count": len(findings), "remaining_interventions": max_interventions},
        )
        selected = next((item for item in findings if item.finding_id == preferred_finding_id), None)
        if self.advisor is not None and findings:
            advised_id = self.advisor.select_finding(findings)
            decision = getattr(self.advisor, "last_decision", {})
            status = str(decision.get("status", "unknown"))
            rationale = str(decision.get("rationale", decision.get("error", "")))[:300]
            self._send(
                "causal_investigator", "supervisor", "llm_investigation_advice",
                {
                    "status": status,
                    "provider": str(decision.get("provider", "unknown")),
                    "model": str(decision.get("model", "unknown")),
                    "selected_finding_id": advised_id or "",
                    "rationale": rationale,
                    "confidence": float(decision.get("confidence", 0.0)) if isinstance(decision.get("confidence", 0.0), (int, float)) else 0.0,
                },
                evidence_refs=sorted({ref for finding in findings for ref in finding.evidence_refs})[:20],
            )
            if advised_id is not None:
                selected = next((item for item in findings if item.finding_id == advised_id), None)
        if selected is None:
            selected = self.investigator.select(findings)
        if selected is None:
            self._send(
                "causal_investigator",
                "supervisor",
                "no_candidate_selected",
                {"reason": "no_findings"},
            )
        else:
            self._send(
                "causal_investigator",
                "supervisor",
                "candidate_selected",
                {
                    "finding_id": selected.finding_id,
                    "risk_type": selected.risk_type,
                    "target_ref": selected.target_ref,
                    "score": selected.score,
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
        self._send(
            "supervisor",
            "supervisor",
            "run_finished",
            {"status": status, "message_count": len(self.messages) + 1},
        )
