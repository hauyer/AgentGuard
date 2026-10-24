"""Pluggable semantic risk-scoring seam.

The default runtime does not call an external LLM. Researchers may inject a scorer that
returns a validated structured assessment from the observed message and policy context.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentguard.contracts.observation import DetectionFinding, Observation


class SemanticDetector:
    def __init__(self, scorer: Callable[[Observation], dict[str, Any]] | None = None, *, threshold: float = 0.65):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        self.scorer = scorer
        self.threshold = threshold

    @property
    def enabled(self) -> bool:
        return self.scorer is not None

    def detect(self, observations: list[Observation]) -> list[DetectionFinding]:
        if self.scorer is None:
            return []
        findings=[]
        for observation in observations:
            assessment = self.scorer(observation)
            if not isinstance(assessment, dict):
                raise TypeError("semantic scorer must return a dictionary")
            score = assessment.get("score", 0.0)
            if not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
                raise ValueError("semantic scorer score must be a finite number in [0, 1]")
            if score < self.threshold:
                continue
            if not observation.target_ref:
                continue
            risk_type = str(assessment.get("risk_type", "semantic_information_flow_risk"))[:128]
            rationale = str(assessment.get("rationale", "External semantic scorer flagged this event."))[:2000]
            target_type = "tool_call" if observation.event_type == "tool_call" else (
                "tool_result" if observation.event_type == "tool_result" else "message"
            )
            findings.append(DetectionFinding(
                risk_type=risk_type,
                severity="high" if score >= 0.85 else "medium",
                target_type=target_type,
                target_ref=observation.target_ref,
                score=float(score),
                rationale=rationale,
                evidence_refs=[observation.event_id],
                detector_version="semantic-callback-0.3",
                score_semantics="external_semantic_scorer_score_uncalibrated_unless_validated",
                alternative_explanations=[str(item)[:512] for item in assessment.get("alternative_explanations", [])[:5]],
                recommended_next_actions=[str(item)[:128] for item in assessment.get("recommended_next_actions", [])[:5]],
            ))
        return findings
