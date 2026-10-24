from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class ArtifactRef(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    artifact_id: str = Field(min_length=64, max_length=64)
    uri: str
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    media_type: str = "application/octet-stream"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ArtifactMetadata(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    artifact_ref: ArtifactRef
    name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
