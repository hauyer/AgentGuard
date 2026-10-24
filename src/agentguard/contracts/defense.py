from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from agentguard.contracts.base import ContractModel

DefenseLifecycleStatus = Literal[
    "proposed", "awaiting_approval", "approved", "submitted", "executed",
    "verification_pending", "verified", "rejected", "unsupported", "failed", "rolled_back",
]


class DefenseCandidate(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    candidate_id: str = Field(default_factory=lambda: f"def-{uuid4().hex[:12]}")
    finding_id: str
    target_type: Literal["message", "tool_call", "tool_result", "activation"]
    target_ref: str
    operation: Literal["block", "replace", "ablate", "patch"]
    scope: Literal["isolated_test_environment", "synthetic_demo"] = "isolated_test_environment"
    expected_risk_reduction: float = Field(ge=0.0, le=1.0)
    estimated_benign_utility_cost: float = Field(ge=0.0, le=1.0)
    reversibility: Literal["reversible", "partially_reversible", "irreversible"] = "reversible"
    approval_required: bool = True
    risk_type: str = "unknown"
    evidence_level: Literal["synthetic", "observational", "interventional", "causal_validated"] = "observational"
    evidence_reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    experience_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    execution_supported: bool = False
    execution_provider: str = "unknown"
    priority_score: float = Field(default=0.0, ge=0.0, le=1.0)
    priority_reasons: list[str] = Field(default_factory=list)
    status: DefenseLifecycleStatus = "proposed"
    rationale: str
    limitations: list[str] = Field(default_factory=list)


class DefensePreview(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    run_id: str
    status: Literal["preview_only", "blocked", "unsupported"] = "preview_only"
    candidates: list[DefenseCandidate] = Field(default_factory=list)
    policy_notes: list[str] = Field(default_factory=list)
    execution_performed: bool = False
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"


class DefenseVerification(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    run_id: str
    candidate_id: str
    status: Literal["not_run", "passed", "failed", "inconclusive"] = "not_run"
    verification_status: Literal["unverified", "verified"] = "unverified"
    provenance_verified: bool = False
    baseline_risk: float | None = Field(default=None, ge=0.0, le=1.0)
    post_defense_risk: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_reduction: float | None = None
    benign_utility_before: float | None = Field(default=None, ge=0.0, le=1.0)
    benign_utility_after: float | None = Field(default=None, ge=0.0, le=1.0)
    false_block_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_transfer_checked: bool = False
    rollback_tested: bool = False
    evidence_mode: Literal["synthetic", "observed", "measured"] = "observed"
    repetitions: int = Field(default=0, ge=0)
    source_artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DefenseStatusEvent(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    from_status: DefenseLifecycleStatus | None = None
    to_status: DefenseLifecycleStatus
    reason: str = Field(min_length=1, max_length=1000)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DefenseLifecycleRecord(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    candidate_id: str
    status: DefenseLifecycleStatus = "proposed"
    execution_scope: Literal["isolated_test_environment", "synthetic_demo"] | None = None
    history: list[DefenseStatusEvent] = Field(default_factory=list)
