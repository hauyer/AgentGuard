from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agentguard.contracts.base import ContractModel


class EvidenceRecord(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    evidence_id: str = Field(default_factory=lambda: f"ev-{uuid4().hex[:12]}")
    kind: Literal["observation", "finding", "hypothesis", "experiment_design", "intervention", "defense", "evaluation", "note"]
    grade: Literal[
        "observational", "synthetic", "interventional", "causal_validated"
    ]
    source: str
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    run_id: str | None = None
    experiment_id: str | None = None
    scenario_id: str | None = None
    source_version: str = "unknown"
    conclusion_scope: str = "supports_only_the_summary_and_linked_artifacts"
    access_level: Literal["public", "agent_private", "research_only", "restricted"] = "research_only"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class CausalValidationCriteria(ContractModel):
    """Auditable experiment records required before evidence can be called causal.

    This structure records what was reviewed. Its fields are not, by themselves,
    proof that the runs, artifacts, or reviewer are authentic; the Evidence Gateway
    must validate those references against a trusted source before emitting a
    causal grade.
    """

    schema_version: Literal["0.2"] = "0.2"
    independent_repetitions: int = Field(ge=2)
    independent_run_ids: list[str] = Field(min_length=2, max_length=100)
    sample_ids: list[str] = Field(min_length=2, max_length=10000)
    experiment_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    matched_control_passed: bool
    matched_control_artifact_ref: str = Field(min_length=1, max_length=2048)
    negative_control_passed: bool
    negative_control_artifact_ref: str = Field(min_length=1, max_length=2048)
    benign_utility_measured: bool
    utility_artifact_ref: str = Field(min_length=1, max_length=2048)
    artifact_hashes_verified: bool
    alternative_explanations_reviewed: bool
    alternative_explanations_review_ref: str = Field(min_length=1, max_length=2048)
    methodology_review_record_ref: str = Field(min_length=1, max_length=2048)
    methodology_reviewer_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def all_checks_passed(self):
        if not all((
            self.matched_control_passed,
            self.negative_control_passed,
            self.benign_utility_measured,
            self.artifact_hashes_verified,
            self.alternative_explanations_reviewed,
        )):
            raise ValueError("causal validation requires every methodological check to pass")
        if len(set(self.independent_run_ids)) != len(self.independent_run_ids):
            raise ValueError("independent_run_ids must be distinct")
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("sample_ids must be distinct")
        if len(self.independent_run_ids) < self.independent_repetitions:
            raise ValueError("each independent repetition must have a recorded run ID")
        return self


class GovernedEvidence(ContractModel):
    """Evidence Gateway view with source, execution and method status kept distinct."""

    schema_version: Literal["0.2"] = "0.2"
    evidence_id: str = Field(min_length=1, max_length=256)
    source_system: str = Field(min_length=1, max_length=128)
    warden_run_id: str | None = None
    warden_plan_id: str | None = None
    target_type: Literal["message", "tool_call", "tool_result", "activation"] | None = None
    target_ref: str | None = Field(default=None, max_length=256)
    scenario_id: str = "unknown"
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_type: Literal[
        "observation", "finding", "hypothesis", "experiment_design",
        "intervention", "defense", "evaluation", "run_summary", "note",
    ]
    evidence_level: Literal[
        "synthetic", "observational", "interventional", "causal_validated",
    ]
    risk_type: str = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)
    verification_status: Literal["verified", "unverified", "blocked", "failed", "unsupported"] = "unverified"
    content_integrity_status: Literal["verified", "unverified", "failed"] = "unverified"
    source_authenticity_status: Literal["verified", "unverified", "unknown"] = "unknown"
    methodology_review_status: Literal["verified", "unverified", "unknown"] = "unknown"
    run_status: Literal["completed", "partial", "failed", "blocked", "unknown"] = "unknown"
    model_version: str = "unknown"
    scenario_version: str = "unknown"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    causal_validation: CausalValidationCriteria | None = None
    # Artifact access capability (C5). Defaults keep existing consumers unchanged:
    # metadata is present, but content access/integrity are never implied.
    metadata_available: bool = True
    content_access_supported: bool = False
    integrity_verified: bool = False
    access_restriction: Literal[
        "read_only_reference", "local_verified_read", "operator_only"
    ] = "read_only_reference"

    @field_validator("artifact_refs")
    @classmethod
    def reject_local_filesystem_references(cls, refs: list[str]) -> list[str]:
        import re
        from pathlib import PurePosixPath, PureWindowsPath
        from urllib.parse import urlsplit

        safe: list[str] = []
        for raw in refs:
            ref = raw.strip()
            try:
                parsed = urlsplit(ref)
            except ValueError as exc:
                raise ValueError("artifact_refs must be valid non-local references") from exc
            if (
                not ref
                or parsed.scheme.lower() == "file"
                or ref.startswith(("\\\\", "//"))
                or bool(re.match(r"^[a-zA-Z]:[\\/]", ref))
                or PureWindowsPath(ref).is_absolute()
                or PurePosixPath(ref).is_absolute()
                or (parsed.scheme.lower() in {"http", "https"} and (parsed.username or parsed.password))
                or (parsed.scheme.lower() in {"http", "https"} and (parsed.query or parsed.fragment))
                or any(ord(char) < 32 for char in ref)
            ):
                raise ValueError("artifact_refs must not expose local paths or credentials")
            safe.append(ref)
        return safe

    @model_validator(mode="after")
    def causal_evidence_requires_method_review(self):
        if (self.target_type is None) != (self.target_ref is None):
            raise ValueError("target_type and target_ref must either both be present or both be absent")
        if self.target_ref is not None:
            from pathlib import PureWindowsPath
            from urllib.parse import urlsplit

            try:
                parsed = urlsplit(self.target_ref)
            except ValueError as exc:
                raise ValueError("target_ref must be a stable non-local identifier") from exc
            if (
                not self.target_ref.strip()
                or PureWindowsPath(self.target_ref).is_absolute()
                or self.target_ref.startswith(("/", "\\\\"))
                or parsed.scheme.lower() == "file"
                or any(ord(char) < 32 for char in self.target_ref)
            ):
                raise ValueError("target_ref must not contain a local path or URI")
        if self.evidence_level == "causal_validated":
            if self.run_status != "completed" or self.verification_status != "verified":
                raise ValueError("causal_validated evidence requires a completed and provenance-verified run")
            if not self.artifact_refs:
                raise ValueError("causal_validated evidence requires at least one verified artifact reference")
            if self.evidence_type not in {"intervention", "evaluation"}:
                raise ValueError("causal_validated evidence must describe an intervention or evaluation")
            if self.causal_validation is None:
                raise ValueError("causal_validated evidence requires explicit methodological checks")
            if self.content_integrity_status != "verified":
                raise ValueError("causal_validated evidence requires verified artifact content integrity")
            if self.source_authenticity_status != "verified":
                raise ValueError("causal_validated evidence requires verified source authenticity")
            if self.methodology_review_status != "verified":
                raise ValueError("causal_validated evidence requires a verified methodology review record")
        return self
