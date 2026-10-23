from typing import Literal
from pydantic import Field
from agentguard.contracts.base import ContractModel


class CapabilityManifest(ContractModel):
    """Explicit runtime capabilities; absence must never be inferred as support."""

    schema_version: Literal["0.1"] = "0.1"
    provider_id: str = Field(min_length=1, max_length=128)
    provider_version: str = "unknown"
    backend_mode: Literal["synthetic", "real"]
    capabilities: dict[str, bool] = Field(default_factory=dict)
    supported_target_types: list[Literal["message", "tool_call", "tool_result", "activation"]] = Field(
        default_factory=list
    )
    supported_operations: list[Literal[
        "block", "replace", "ablate", "patch"
    ]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    def supports(self, capability: str) -> bool:
        return self.capabilities.get(capability, False)

    def supports_intervention(self, target_type: str, operation: str) -> bool:
        return (
            target_type in self.supported_target_types
            and operation in self.supported_operations
        )
