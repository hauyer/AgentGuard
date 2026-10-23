"""Contracts for local white-box activation intervention experiments."""
from typing import Literal
from pydantic import Field, model_validator
from agentguard.contracts.base import ContractModel


class ActivationPatchSpec(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    target_module: str = Field(min_length=1, max_length=512)
    operation: Literal["zero", "replace_from_reference", "add_noise"]
    token_positions: list[int] = Field(min_length=1, max_length=512)
    seed: int = 7
    noise_std: float = Field(default=0.01, ge=0.0, le=10.0)
    max_new_tokens: int = Field(default=64, ge=1, le=4096)
    use_cache: Literal[False] = False
    experiment_id: str = Field(min_length=1, max_length=256)
    authorization_ref: str = Field(min_length=1, max_length=256)
    scope: Literal["isolated_test_environment"] = "isolated_test_environment"

    @model_validator(mode="after")
    def validate_positions(self):
        if any(position < 0 for position in self.token_positions):
            raise ValueError("token_positions must be non-negative")
        if len(set(self.token_positions)) != len(self.token_positions):
            raise ValueError("token_positions must not contain duplicates")
        if self.operation == "add_noise" and self.noise_std <= 0:
            raise ValueError("add_noise requires noise_std > 0")
        return self
