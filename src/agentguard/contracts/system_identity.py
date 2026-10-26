"""Public, non-secret runtime identity contract."""

from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class SystemIdentityResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    software_version: str = Field(min_length=1, max_length=128)
    api_contract_version: str = Field(min_length=1, max_length=128)
    build_commit: str = Field(min_length=1, max_length=64)
    build_commit_source: Literal["AGENTGUARD_BUILD_COMMIT", "unknown"]
    runtime_identity_status: Literal["declared", "unknown"]
    openapi_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    openapi_hash_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    capabilities: dict[str, str] = Field(default_factory=dict)
