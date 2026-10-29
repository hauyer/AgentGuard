"""Strict public result manifest imported from a verified WARDEN artifact."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, FiniteFloat, model_validator

from agentguard.contracts.base import ContractModel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ResearchMetricValue(ContractModel):
    value: FiniteFloat
    denominator: int = Field(gt=0)


class WardenResearchManifest(ContractModel):
    schema_version: Literal["warden.research-run/1"]
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    protocol_ref: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    config_sha256: Sha256
    executor_sha256: Sha256
    dataset_sha256: Sha256
    split_sha256: Sha256
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    seed: int
    started_at: AwareDatetime
    finished_at: AwareDatetime
    status: Literal["completed"]
    provenance: Literal["local_real_model"]
    track: Literal["mechanism_testbed"]
    files_sha256: dict[str, Sha256] = Field(min_length=1)
    model_files_sha256: dict[str, Sha256] = Field(min_length=1)
    metrics: dict[str, ResearchMetricValue] = Field(min_length=1)
    missing_metrics: dict[str, str]
    dependencies: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_times(self):
        if self.finished_at < self.started_at:
            raise ValueError("Manifest completion precedes start")
        if any(value.utcoffset().total_seconds() != 0
               for value in (self.started_at, self.finished_at)):
            raise ValueError("Manifest timestamps must be UTC")
        for name in (*self.files_sha256, *self.model_files_sha256):
            if name.startswith(("/", "\\")) or ".." in name.split("/") or ":" in name:
                raise ValueError("Manifest file keys must be safe relative identifiers")
        return self
