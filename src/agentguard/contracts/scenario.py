from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel


class ScenarioSpec(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    scenario_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    track: Literal["core", "transfer", "stego", "application", "demo"] = "demo"
    description: str
    allowed_targets: list[Literal["message", "tool_call", "tool_result", "activation"]] = Field(
        default_factory=lambda: ["message", "tool_result"]
    )
    capabilities_required: list[str] = Field(default_factory=list)
    public_context: dict[str, str | int | float | bool] = Field(default_factory=dict)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
