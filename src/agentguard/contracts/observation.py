from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import Field, model_validator
from agentguard.contracts.base import ContractModel


class Observation(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    event_id: str = Field(default_factory=lambda: f"evt-{uuid4().hex[:12]}")
    experiment_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: str
    event_type: Literal[
        "message", "tool_call", "tool_result", "decision",
        "model_activation", "risk_signal", "runtime_status"
    ]
    source: str | None = None
    target: str | None = None
    target_ref: str | None = None
    summary: str
    sensitivity: list[str] = Field(default_factory=list)
    # Features are observed data, not labels or hidden evaluator truth.
    features: dict[str, str | int | float | bool] = Field(default_factory=dict)
    visibility: Literal["public", "agent_private", "research_only"] = "research_only"
    provenance: str = "unknown"

    @model_validator(mode="after")
    def reject_hidden_truth_features(self):
        forbidden = {
            "truth", "label", "labels", "oracle", "is_positive", "target_label",
            "is_true_stego", "oracle_label", "ground_truth_path", "trial_truth",
            "ground_truth", "ground_truth_label", "oracle_secret", "hidden_label", "truth_label",
            "answer_key",
        }
        present = forbidden.intersection(key.lower().replace("-", "_") for key in self.features)
        if present:
            raise ValueError(f"Observation must not contain hidden truth features: {sorted(present)}")
        return self


class DetectionFinding(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    finding_id: str = Field(default_factory=lambda: f"find-{uuid4().hex[:10]}")
    risk_type: str
    severity: Literal["low", "medium", "high", "critical"]
    target_type: Literal["message", "tool_call", "tool_result", "activation"]
    target_ref: str
    score: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    detector_version: str = "rules-0.2"
    score_semantics: str = "uncalibrated_screening_score"
    alternative_explanations: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
