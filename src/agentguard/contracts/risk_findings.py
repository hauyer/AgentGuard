"""Versioned contract for metadata-based risk findings and hypotheses (v1.0).

Findings are explainable structural signals over authorized observation
metadata. They never claim content-level secrets, causal mediation or leak
confirmation; every claim stays a falsifiable hypothesis pending verification.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel

RiskFindingMethod = Literal[
    "collaboration_concentration", "relay_path_candidate",
    "temporal_order_anomaly", "external_flow_candidate",
    "tool_use_pattern_candidate", "insufficient_evidence",
]


class RiskFinding(ContractModel):
    """A candidate risk signal derived from observable interaction metadata."""

    schema_version: Literal["1.0"] = "1.0"
    finding_id: str = Field(min_length=1, max_length=128)
    investigation_id: str = Field(min_length=1, max_length=256)
    risk_type: str = Field(min_length=1, max_length=128)
    detection_method: RiskFindingMethod
    description: str = Field(min_length=1, max_length=2048)
    target_ref: str = Field(min_length=1, max_length=256)
    supporting_evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    contradicting_evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    calibration: Literal["uncalibrated_structural_signal", "not_applicable"] = (
        "uncalibrated_structural_signal"
    )
    status: Literal["candidate", "insufficient_evidence"] = "candidate"
    evidence_level: Literal["synthetic", "observational"] = "observational"
    verification_status: Literal["unverified"] = "unverified"
    limitations: list[str] = Field(default_factory=list, max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RiskHypothesis(ContractModel):
    """A falsifiable investigation hypothesis raised from one risk finding."""

    schema_version: Literal["1.0"] = "1.0"
    hypothesis_id: str = Field(min_length=1, max_length=128)
    investigation_id: str = Field(min_length=1, max_length=256)
    finding_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=8, max_length=2048)
    expected_effect: str = Field(min_length=4, max_length=2048)
    supporting_evidence: list[str] = Field(default_factory=list, max_length=64)
    contradicting_evidence: list[str] = Field(default_factory=list, max_length=64)
    next_check: str = Field(min_length=4, max_length=256)
    required_capabilities: list[str] = Field(default_factory=list, max_length=16)
    intervention_supported: bool = False
    stop_condition: str = Field(min_length=4, max_length=512)
    status: Literal["proposed"] = "proposed"


class RiskAnalysisReport(ContractModel):
    """Finding/hypothesis bundle for one investigation workflow."""

    schema_version: Literal["1.0"] = "1.0"
    investigation_id: str = Field(min_length=1, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    available: bool
    findings: list[RiskFinding] = Field(default_factory=list, max_length=128)
    hypotheses: list[RiskHypothesis] = Field(default_factory=list, max_length=128)
    limitations: list[str] = Field(default_factory=list, max_length=32)
