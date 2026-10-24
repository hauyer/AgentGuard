"""Budget-aware hypothesis generation and paired experiment design."""
from __future__ import annotations

from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.hypothesis import (
    InvestigationAction,
    InvestigationHypothesis,
    PairedExperimentDesign,
)
from agentguard.contracts.observation import DetectionFinding, Observation


class ActiveInvestigator:
    """Transparent heuristic planner; scores are prioritization signals, not probabilities."""
    def generate_hypotheses(
        self, request: ExperimentRequest, findings: list[DetectionFinding], observations: list[Observation]
    ) -> list[InvestigationHypothesis]:
        by_event = {item.event_id: item for item in observations}
        hypotheses: list[InvestigationHypothesis] = []
        for finding in findings:
            evidence = [ref for ref in finding.evidence_refs if ref in by_event]
            alternatives = list(finding.alternative_explanations) or [
                "The observed surface pattern may be benign or unrelated to the suspected information flow.",
                "The policy metadata may be incomplete or stale."
            ]
            statement = (
                f"Observed event at target {finding.target_ref!r} is associated with "
                f"{finding.risk_type}; targeted intervention should change the measured risk "
                f"without unacceptable benign-utility loss."
            )
            hypotheses.append(InvestigationHypothesis(
                experiment_id=request.experiment_id,
                finding_id=finding.finding_id,
                target_ref=finding.target_ref,
                statement=statement,
                expected_effect="Risk exposure decreases under matched treatment while benign utility remains within tolerance.",
                supporting_evidence=evidence,
                contradicting_evidence=[],
                alternative_explanations=alternatives,
                confidence=max(0.0, min(1.0, finding.score)),
                status="proposed",
                next_test="matched_paired_replay",
            ))
        return hypotheses

    def rank_actions(
        self,
        hypotheses: list[InvestigationHypothesis],
        findings: list[DetectionFinding],
        capabilities: CapabilityManifest,
        *,
        risk_weight: float = 1.0,
    ) -> list[InvestigationAction]:
        finding_map = {item.finding_id: item for item in findings}
        operations_by_target = {
            "message": ["block", "replace"],
            "tool_call": ["block", "replace"],
            "tool_result": ["replace", "block"],
            "activation": ["ablate", "patch"],
        }
        costs = {"block": 1.0, "replace": 1.2, "ablate": 3.0, "patch": 4.0}
        risks = {"block": 0.18, "replace": 0.12, "ablate": 0.45, "patch": 0.55}
        actions: list[InvestigationAction] = []
        for hypothesis in hypotheses:
            finding = finding_map.get(hypothesis.finding_id)
            if finding is None:
                continue
            for operation in operations_by_target.get(finding.target_type, []):
                if not capabilities.supports_intervention(finding.target_type, operation):
                    continue
                info_gain = min(1.0, 0.25 + 0.65 * finding.score + (0.1 if operation == "replace" else 0.0))
                cost = costs[operation]
                risk = risks[operation]
                score = info_gain / (cost + risk_weight * risk)
                actions.append(InvestigationAction(
                    hypothesis_id=hypothesis.hypothesis_id,
                    target_type=finding.target_type,
                    target_ref=finding.target_ref,
                    operation=operation,
                    expected_information_gain=round(info_gain, 6),
                    estimated_cost=cost,
                    estimated_risk=risk,
                    utility_score=round(score, 6),
                    rationale=("Heuristic ranking uses finding score, estimated operation cost, and side-effect risk; "
                               "it is not a calibrated expected-information-gain estimate."),
                ))
        return sorted(actions, key=lambda action: action.utility_score, reverse=True)

    def design_paired_experiment(
        self, request: ExperimentRequest, hypothesis: InvestigationHypothesis, action: InvestigationAction,
        *, repetitions: int = 5, primary_metric: str = "risk_exposure"
    ) -> PairedExperimentDesign:
        repetitions = max(2, min(10000, repetitions))
        seeds = [request.seed + offset for offset in range(repetitions)]
        return PairedExperimentDesign(
            experiment_id=request.experiment_id,
            hypothesis_id=hypothesis.hypothesis_id,
            design_type="paired_replay",
            treatment_operation=action.operation,
            control_operation="no_intervention",
            negative_control_operation="sham_intervention",
            seed=request.seed,
            seeds=seeds,
            repetitions=repetitions,
            matched_on=["scenario_id", "case_family", "seed", "model_version", "task_input", "runtime_config"],
            primary_metric=primary_metric,
            secondary_metrics=["benign_utility", "false_block_rate", "latency_seconds"],
            preregistered=False,
            notes=[
                "This is an experiment plan, not evidence that the paired runs were executed.",
                "Random seeds are enumerated for reproducibility; actual backend determinism must be verified.",
                "Report treatment/control failures and missing pairs; do not silently discard them."
            ],
        )
