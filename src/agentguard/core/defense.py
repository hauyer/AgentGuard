"""Minimal-scope defense preview and evidence-aware verification helpers."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from agentguard.contracts.defense import (
    DefenseCandidate,
    DefenseLifecycleRecord,
    DefensePreview,
    DefenseStatusEvent,
    DefenseVerification,
)
from agentguard.contracts.evidence import GovernedEvidence
from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.report import RunReport
from agentguard.core.statistics import paired_effect_summary

_ALLOWED_DEFENSE_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"awaiting_approval", "approved", "submitted", "rejected", "unsupported"},
    "awaiting_approval": {"approved", "rejected", "unsupported"},
    "approved": {"submitted", "rejected", "unsupported"},
    "submitted": {"executed", "failed", "unsupported", "rejected"},
    "executed": {"verification_pending", "failed", "rolled_back"},
    "verification_pending": {"verified", "failed", "rejected", "rolled_back"},
    "verified": {"rolled_back"},
    "rejected": set(),
    "unsupported": set(),
    "failed": {"rolled_back"},
    "rolled_back": set(),
}


class DefenseTransitionError(ValueError):
    """Raised when a defense workflow attempts an illegal state transition."""


class DefensePlannerService:
    @staticmethod
    def initial_lifecycle(candidate: DefenseCandidate) -> DefenseLifecycleRecord:
        event = DefenseStatusEvent(
            from_status=None,
            to_status="proposed",
            reason="Candidate was generated from a finding; no approval or execution is implied.",
        )
        return DefenseLifecycleRecord(
            candidate_id=candidate.candidate_id,
            status="proposed",
            execution_scope=candidate.scope,
            history=[event],
        )

    @staticmethod
    def transition(
        record: DefenseLifecycleRecord,
        status: str,
        reason: str,
    ) -> DefenseLifecycleRecord:
        if status == record.status:
            return record
        if status not in _ALLOWED_DEFENSE_TRANSITIONS.get(record.status, set()):
            raise DefenseTransitionError(f"invalid defense transition {record.status!r} -> {status!r}")
        event = DefenseStatusEvent(from_status=record.status, to_status=status, reason=reason)
        return record.model_copy(update={"status": status, "history": [*record.history, event]})

    def preview(
        self,
        report: RunReport,
        *,
        experiences: Iterable[ExperienceRecord] = (),
    ) -> DefensePreview:
        manifest = report.capability_manifest
        candidates: list[DefenseCandidate] = []
        notes = [
            "Preview only: no runtime operation was executed.",
            "Expected risk reduction is a heuristic estimate, not a measured outcome.",
        ]
        if manifest is None:
            return DefensePreview(run_id=report.run_id, status="unsupported", policy_notes=["Run report has no capability manifest."])
        verified_experiences = [
            item for item in experiences
            if item.verification_status == "verified"
            and item.allow_auto_recommendation
            and item.outcome == "success"
            and item.evidence_grade in {"interventional", "causal_validated"}
        ]
        for finding in report.findings:
            operations = {"message": ["block", "replace"], "tool_call": ["block", "replace"],
                          "tool_result": ["replace", "block"], "activation": ["ablate", "patch"]}.get(finding.target_type, [])
            for operation in operations:
                if not manifest.supports_intervention(finding.target_type, operation):
                    continue
                cost = {"block": 0.12, "replace": 0.08, "ablate": 0.4, "patch": 0.5}[operation]
                candidate_id = "def-" + hashlib.sha256(
                    f"{report.run_id}|{finding.finding_id}|{finding.target_type}|{finding.target_ref}|{operation}".encode()
                ).hexdigest()[:12]
                action_type = f"{finding.target_type}_{operation}"
                experience_refs = [
                    item.experience_id for item in verified_experiences
                    if item.scenario_id == report.scenario_id
                    and item.risk_type == finding.risk_type
                    and item.action_type == action_type
                ]
                evidence_level = "synthetic" if manifest.backend_mode == "synthetic" else "observational"
                evidence_reliability = 0.1 if evidence_level == "synthetic" else 0.35
                reversible = operation in {"block", "replace"}
                priority = (
                    finding.score * evidence_reliability
                    + min(0.1, len(experience_refs) * 0.02)
                    + (0.08 if reversible else 0.0)
                    - cost * 0.25
                    - (0.05 if manifest.backend_mode == "real" else 0.0)
                )
                priority_score = round(max(0.0, min(1.0, priority)), 4)
                priority_reasons = [
                    f"Risk impact input is the uncalibrated finding score ({finding.score:.3f}).",
                    f"Evidence reliability factor is a conservative {evidence_level} heuristic ({evidence_reliability:.2f}).",
                    f"Estimated action cost is {cost:.2f}.",
                    "Action is reversible." if reversible else "Action is only partially reversible.",
                    "A real backend requires operator approval." if manifest.backend_mode == "real" else "No real-backend approval is inferred from this synthetic preview.",
                    f"{len(experience_refs)} matching verified experience record(s).",
                ]
                candidates.append(DefenseCandidate(
                    candidate_id=candidate_id,
                    finding_id=finding.finding_id,
                    target_type=finding.target_type,
                    target_ref=finding.target_ref,
                    operation=operation,
                    scope="synthetic_demo" if manifest.backend_mode == "synthetic" else "isolated_test_environment",
                    expected_risk_reduction=round(min(0.9, finding.score * (0.85 if operation == "block" else 0.65)), 4),
                    estimated_benign_utility_cost=cost,
                    reversibility="reversible" if reversible else "partially_reversible",
                    approval_required=manifest.backend_mode == "real",
                    risk_type=finding.risk_type,
                    evidence_level=evidence_level,
                    evidence_reliability=evidence_reliability,
                    experience_refs=experience_refs,
                    evidence_refs=list(finding.evidence_refs),
                    execution_supported=manifest.supports_intervention(finding.target_type, operation),
                    execution_provider=manifest.provider_id,
                    priority_score=priority_score,
                    priority_reasons=priority_reasons,
                    rationale=(f"Candidate targets finding {finding.finding_id}; choose the narrowest action that "
                               "can test the hypothesis and compare benign-task utility."),
                    limitations=["Risk reduction, evidence reliability, cost and priority are ranking heuristics only.",
                                 "No counterfactual run has been executed."],
                ))
        candidates.sort(key=lambda item: (item.priority_score, item.candidate_id), reverse=True)
        return DefensePreview(
            run_id=report.run_id,
            status="preview_only" if candidates else "unsupported",
            candidates=candidates,
            policy_notes=notes if candidates else notes + ["No finding maps to an explicitly supported target/operation."],
            execution_performed=False,
            evidence_mode="synthetic" if report.backend_mode == "synthetic" else "observed",
        )

    def preview_from_evidence(
        self,
        *,
        run_id: str,
        evidence: Iterable[GovernedEvidence],
        run_status: str,
        experiences: Iterable[ExperienceRecord] = (),
    ) -> DefensePreview:
        """Propose upper-layer defenses from completed WARDEN evidence only.

        WARDEN currently exposes no supported executor for these application-level
        policy actions. Candidates are therefore proposals with execution disabled.
        """
        evidence_items = list(evidence)
        if run_status != "completed":
            return DefensePreview(
                run_id=run_id,
                status="blocked",
                policy_notes=[f"WARDEN run status is {run_status}; defense candidates require a completed run."],
                evidence_mode="observed",
            )
        records = [
            item for item in experiences
            if item.verification_status == "verified"
            and item.allow_auto_recommendation
            and item.outcome == "success"
            and item.evidence_grade in {"interventional", "causal_validated"}
        ]
        candidates: list[DefenseCandidate] = []
        notes = [
            "Candidates are strategy proposals generated from governed WARDEN evidence.",
            "No public WARDEN or AgentGuard external policy executor is declared; execution is unsupported.",
        ]
        target_operations = {
            "message": ("block", "replace"),
            "tool_call": ("block",),
            "tool_result": ("replace", "block"),
        }
        reliability = {
            "synthetic": 0.05,
            "observational": 0.25,
            "interventional": 0.55,
            "causal_validated": 0.75,
        }
        costs = {"block": 0.12, "replace": 0.08}
        seen: set[tuple[str, str, str]] = set()
        for item in evidence_items:
            target_type = item.target_type
            target_ref = item.target_ref
            if target_type not in target_operations or not target_ref or item.run_status != "completed":
                if target_type == "activation":
                    notes.append("Activation targets are not emitted as application-layer defenses; they remain WARDEN-owned.")
                continue
            matching_experiences = [
                record for record in records
                if record.scenario_id == item.scenario_id
                and record.risk_type == item.risk_type
            ]
            weight = reliability[item.evidence_level]
            for operation in target_operations[target_type]:
                key = (item.evidence_id, target_ref, operation)
                if key in seen:
                    continue
                seen.add(key)
                cost = costs[operation]
                action_type = f"{target_type}_{operation}"
                experience_refs = [
                    record.experience_id for record in matching_experiences
                    if record.action_type == action_type
                ]
                priority = weight * 0.15 + min(0.1, len(experience_refs) * 0.02) + 0.08 - cost * 0.25
                candidate_id = "def-" + hashlib.sha256(
                    f"{run_id}|{item.evidence_id}|{target_ref}|{operation}".encode()
                ).hexdigest()[:12]
                candidates.append(DefenseCandidate(
                    candidate_id=candidate_id,
                    finding_id=item.evidence_id,
                    target_type=target_type,
                    target_ref=target_ref,
                    operation=operation,
                    scope="isolated_test_environment",
                    expected_risk_reduction=0.0,
                    estimated_benign_utility_cost=cost,
                    reversibility="reversible",
                    approval_required=True,
                    risk_type=item.risk_type,
                    evidence_level=item.evidence_level,
                    evidence_reliability=weight,
                    experience_refs=experience_refs,
                    evidence_refs=[item.evidence_id, *item.artifact_refs],
                    execution_supported=False,
                    execution_provider="unsupported: no declared policy executor",
                    priority_score=round(max(0.0, min(1.0, priority)), 4),
                    priority_reasons=[
                        "Impact and risk-reduction magnitude are unknown; no calibrated estimate is emitted.",
                        f"Evidence reliability ranking factor: {weight:.2f} for {item.evidence_level} evidence.",
                        f"Estimated utility cost heuristic: {cost:.2f}.",
                        "Candidate action is narrow and reversible.",
                        f"{len(experience_refs)} matching verified experience record(s).",
                        "Execution capability is absent, so this candidate is not executable.",
                    ],
                    rationale=f"Proposed from WARDEN evidence {item.evidence_id} for risk type {item.risk_type}.",
                    limitations=[
                        "This is a policy candidate, not an approved or executed defense.",
                        "Evidence references were not treated as access to their underlying files.",
                        "Effect on normal-task utility and risk transfer is not measured.",
                    ],
                ))
        candidates.sort(key=lambda item: (item.priority_score, item.candidate_id), reverse=True)
        return DefensePreview(
            run_id=run_id,
            status="preview_only" if candidates else "unsupported",
            candidates=candidates,
            policy_notes=notes if candidates else notes + ["No completed WARDEN evidence contained a supported application-layer target reference."],
            execution_performed=False,
            evidence_mode="synthetic" if any(item.evidence_level == "synthetic" for item in evidence_items) else "observed",
        )

    def verify_paired(
        self,
        report: RunReport,
        candidate: DefenseCandidate,
        baseline_risk: list[float],
        post_defense_risk: list[float],
        baseline_utility: list[float] | None = None,
        post_defense_utility: list[float] | None = None,
        *, seed: int = 7,
    ) -> DefenseVerification:
        risk = paired_effect_summary(baseline_risk, post_defense_risk, seed=seed)
        utility = None
        if baseline_utility is not None or post_defense_utility is not None:
            if baseline_utility is None or post_defense_utility is None:
                raise ValueError("both baseline and post-defense utility arrays are required")
            utility = paired_effect_summary(baseline_utility, post_defense_utility, seed=seed + 1)
        reduction = -risk["mean_treatment_minus_control"]
        risk_direction_supported = risk["bootstrap_95_ci"][1] < 0.0
        utility_preserved = utility is not None and utility["bootstrap_95_ci"][0] >= -0.1
        if risk_direction_supported and utility_preserved:
            verification_status = "passed"
        elif risk["mean_treatment_minus_control"] >= 0.0 or (
            utility is not None and utility["mean_treatment_minus_control"] < -0.1
        ):
            verification_status = "failed"
        else:
            verification_status = "inconclusive"
        return DefenseVerification(
            run_id=report.run_id,
            candidate_id=candidate.candidate_id,
            status=verification_status,
            verification_status="unverified",
            provenance_verified=False,
            baseline_risk=risk["control_mean"],
            post_defense_risk=risk["treatment_mean"],
            risk_reduction=reduction,
            benign_utility_before=utility["control_mean"] if utility else None,
            benign_utility_after=utility["treatment_mean"] if utility else None,
            false_block_rate=None,
            risk_transfer_checked=False,
            rollback_tested=False,
            evidence_mode="synthetic" if report.backend_mode == "synthetic" else "observed",
            repetitions=len(baseline_risk),
            source_artifact_refs=[],
            evidence_refs=[],
            limitations=[
                "Verification was computed from caller-supplied paired arrays; raw runs and provenance must be retained.",
                "A statistical pass is not a verified defense outcome without matching, hash-checked experiment artifacts.",
                "Risk transfer and rollback were not tested by this statistical helper.",
                "A pass requires the bootstrap risk interval to be below zero and the utility interval to stay above the default -0.1 margin.",
                "The threshold for benign utility loss is a default guardrail, not a universal safety criterion."
            ],
        )
