"""Evidence-constrained defense planning over existing candidates (D4).

Wraps `DefensePlannerService.preview_from_evidence` (unchanged) with transparent
ranking factors and per-candidate verification plans. Ranking inputs without a
real measurement stay ``None``; verification plans stay ``not_executed`` while
no intervention executor, paired replay or independent evaluation exists. The
planner never approves, submits or executes anything.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.defense import DefenseCandidate
from agentguard.contracts.defense_planning import (
    DefenseRankingFactors,
    DefenseVerificationPlan,
    GovernedDefenseCandidatePlan,
    GovernedDefensePlan,
)
from agentguard.core.defense import DefensePlannerService
from agentguard.services.evidence_gateway import EvidenceGateway

# Policy-expression catalogue: upper-layer strategy wording only. These are
# candidate policies, never claims about kernel executability.
_POLICY_EXPRESSIONS = {
    "block": "Restrict communication on the implicated edge (block candidate).",
    "replace": "Apply a field-level redaction/replacement rule on the target.",
    "ablate": "Narrow the implicated agent's tool/permission surface.",
    "patch": "Require approval or human review before the sensitive operation.",
}


class DefensePlanningService:
    """Build governed defense plans from governed evidence."""

    def __init__(self, artifacts: LocalArtifactStore) -> None:
        self.artifacts = artifacts
        self.planner = DefensePlannerService()

    def build_plan(
        self,
        request_id: str,
        entry: Mapping[str, Any],
        *,
        evidence_gateway: EvidenceGateway,
    ) -> GovernedDefensePlan:
        governed = evidence_gateway.for_warden_workflow(
            entry, artifacts=self.artifacts,
        )
        run_id = entry.get("run_id")
        limitations: list[str] = [
            (
                "Candidates are policy recommendations only; existence of a "
                "candidate is not evidence of effectiveness."
            ),
        ]
        items = governed.get("items", [])
        if not items:
            limitations.append(
                "No governed evidence items; no defense candidates were generated."
            )
            return self._persist(GovernedDefensePlan(
                request_id=request_id,
                run_id=run_id if isinstance(run_id, str) else None,
                candidates=[],
                limitations=limitations,
            ))

        from agentguard.contracts.evidence import GovernedEvidence
        evidence_records = [GovernedEvidence.model_validate(item) for item in items]
        preview = self.planner.preview_from_evidence(
            run_id=str(run_id or request_id),
            evidence=evidence_records,
            run_status=governed.get("run_status", "unknown"),
            experiences=[],
        )
        candidates: list[GovernedDefenseCandidatePlan] = []
        for candidate in preview.candidates:
            ranking = self._ranking(candidate, evidence_records)
            verification = self._verification_plan(candidate, ranking)
            candidates.append(GovernedDefenseCandidatePlan(
                candidate_id=candidate.candidate_id,
                operation=candidate.operation,
                target_ref=candidate.target_ref,
                policy_expression=_POLICY_EXPRESSIONS.get(
                    candidate.operation,
                    f"Candidate {candidate.operation} policy on {candidate.target_ref}.",
                ),
                ranking=ranking,
                verification_plan=verification,
            ))
        if not candidates:
            limitations.append(
                "The planner proposed no candidates for the available evidence."
            )
        return self._persist(GovernedDefensePlan(
            request_id=request_id,
            run_id=run_id if isinstance(run_id, str) else None,
            candidates=candidates,
            overall_execution_supported=False,
            limitations=limitations,
        ))

    # ------------------------------------------------------------------ #
    # ranking and verification planning
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ranking(
        candidate: DefenseCandidate, evidence_records: list[Any],
    ) -> DefenseRankingFactors:
        unknown: list[str] = []
        supporting = [
            record for record in evidence_records
            if candidate.target_ref in " ".join(
                filter(None, [record.target_ref or ""])
            ) or record.target_ref == candidate.target_ref
        ]
        if supporting:
            verified = sum(
                1 for record in supporting if record.verification_status == "verified"
            )
            sufficiency = min(1.0, verified / 3.0) if verified else 0.2
        else:
            sufficiency = None
            unknown.append("evidence_sufficiency")
        relevance = None
        if candidate.evidence_refs:
            relevance = min(1.0, 0.3 + 0.1 * len(candidate.evidence_refs))
        else:
            unknown.append("risk_relevance")
        return DefenseRankingFactors(
            risk_relevance=relevance,
            evidence_sufficiency=sufficiency,
            expected_effect=candidate.expected_risk_reduction,
            benign_utility_cost=candidate.estimated_benign_utility_cost,
            execution_cost=None,  # no real executor cost measurements exist
            scope_of_impact=(
                "single_edge" if candidate.target_type == "message" else "single_agent"
            ),
            approval_required=candidate.approval_required,
            reversibility=candidate.reversibility,
            execution_supported=False,
            unknown_fields=unknown + ["execution_cost"],
        )

    @staticmethod
    def _verification_plan(
        candidate: DefenseCandidate, ranking: DefenseRankingFactors,
    ) -> DefenseVerificationPlan:
        digest = hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest()[:10]
        executability = "blocked_no_intervention_executor"
        limitations = [
            (
                "No public intervention executor is declared (DEP-007); this plan "
                "has not been executed and reports no effect."
            ),
        ]
        if candidate.reversibility == "irreversible":
            limitations.append(
                "Irreversible candidates require explicit operator sign-off "
                "before any future execution request."
            )
        return DefenseVerificationPlan(
            verification_plan_id=f"dvp-{digest}",
            candidate_id=candidate.candidate_id,
            treatment_group=(
                f"Matched replays with the '{candidate.operation}' policy applied "
                f"to {candidate.target_ref}, fixed scenario/seed/model."
            ),
            control_group=(
                "Matched replays without the policy, identical scenario, seed "
                "and model configuration."
            ),
            fixed_factors=["scenario", "seed", "model_id", "runtime_id"],
            risk_metrics=["risk_exposure", "candidate_edge_activity"],
            utility_metrics=["benign_utility", "normal_task_success_rate"],
            repetitions=2,
            exit_conditions=[
                "Both paired replays complete with verified provenance, or",
                "risk metrics show no change beyond noise and the candidate is shelved.",
            ],
            required_capabilities=[
                "intervention.executor", "paired_replay", "independent_evaluation",
            ],
            executability=executability,
            execution_status="not_executed",
            limitations=limitations,
        )

    def _persist(self, plan: GovernedDefensePlan) -> GovernedDefensePlan:
        artifact = self.artifacts.put(
            plan.model_dump_json(indent=2).encode("utf-8"),
            name=f"defense-plan-{plan.request_id}.json",
            description="Evidence-constrained defense plan with verification plans",
            tags=["defense-plan", "proposal-only"],
            metadata={"request_id": plan.request_id,
                      "candidates": len(plan.candidates)},
            media_type="application/json",
        )
        plan.plan_artifact_id = artifact.artifact_ref.artifact_id
        return plan
