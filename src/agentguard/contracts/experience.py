from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import Field, model_validator
from agentguard.contracts.base import ContractModel


class ExperienceRecord(ContractModel):
    """Versioned case memory. Unverified records are advisory only."""
    schema_version: Literal["0.1"] = "0.1"
    experience_id: str = Field(default_factory=lambda: f"exp-mem-{uuid4().hex[:12]}")
    scenario_id: str
    case_family: str = "unspecified"
    risk_type: str
    hypothesis_summary: str
    action_type: str
    action_summary: str
    outcome: Literal["success", "partial", "failure", "inconclusive"]
    evidence_grade: Literal["observational", "synthetic", "interventional", "causal_validated"]
    verification_status: Literal["unverified", "reviewed", "verified"] = "unverified"
    repetitions: int = Field(default=1, ge=1)
    evidence_refs: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    model_version: str = "unknown"
    code_version: str = "unknown"
    seed: int | None = None
    allow_auto_recommendation: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_record(self):
        if self.verification_status == "verified":
            if self.repetitions < 2:
                raise ValueError("verified experiences require at least two repetitions")
            if not self.evidence_refs:
                raise ValueError("verified experiences require evidence_refs")
            if not self.validation_notes:
                raise ValueError("verified experiences require validation_notes")
            if self.evidence_grade not in {"interventional", "causal_validated"}:
                raise ValueError("verified experiences require interventional evidence or stronger")
        if self.allow_auto_recommendation and self.verification_status != "verified":
            raise ValueError("only verified experiences may be auto-recommended")
        return self


class ExperienceSearchResult(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    query: str = ""
    items: list[ExperienceRecord] = Field(default_factory=list)
    total: int = Field(ge=0)
    recommendation_note: str = "Unverified memory is advisory and must not trigger execution."
