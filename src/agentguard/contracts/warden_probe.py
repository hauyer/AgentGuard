"""AgentGuard's versioned contract for WARDEN's white-box Probe CLI config.

This describes WARDEN's public JSON configuration only. It does not implement
model loading, activation capture, Probe training, or remote command execution.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from agentguard.contracts.base import ContractModel


class WardenProbeModelConfig(ContractModel):
    model_path: str = Field(min_length=1)
    tokenizer_path: str | None = Field(default=None, min_length=1)
    revision: str | None = Field(default=None, min_length=1)
    device: str = Field(default="auto", min_length=1)
    dtype: Literal["auto", "float32", "float16", "bfloat16"] = "auto"
    trust_remote_code: bool = False
    local_files_only: bool = True
    use_safetensors: bool = True
    parameter_gradients: Literal[False] = False
    quantization: Literal["none", "bnb_4bit", "bnb_8bit"] = "none"
    bnb_4bit_compute_dtype: Literal["float32", "float16", "bfloat16"] = "bfloat16"
    bnb_4bit_quant_type: Literal["nf4", "fp4"] = "nf4"
    bnb_4bit_use_double_quant: bool = True


class WardenProbeCaptureTarget(ContractModel):
    target_id: str = Field(min_length=1)
    agent_ids: list[str] = Field(min_length=1)
    module: str | None = Field(default=None, min_length=1)
    layer: int | None = Field(default=None, ge=0)
    component: str | None = "resid_post"
    token_policy: Literal["all", "last_token", "mean_tokens"] = "last_token"
    retain_gradient: Literal[False] = False

    @field_validator("agent_ids")
    @classmethod
    def unique_nonblank_agent_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("agent_ids must be unique nonblank identifiers")
        return cleaned

    @model_validator(mode="after")
    def require_coordinate(self) -> WardenProbeCaptureTarget:
        if self.module is None and self.layer is None:
            raise ValueError("capture target must declare module or layer")
        return self


class WardenProbeTrainingConfig(ContractModel):
    c: float = Field(default=1.0, gt=0)
    max_iter: int = Field(default=2000, ge=1)
    random_seed: int = 0


class WardenWhiteboxProbeExperimentConfig(ContractModel):
    """Exact local configuration shape accepted by WARDEN's whitebox CLI."""

    schema_version: Literal["warden.whitebox-probe-experiment/1"]
    experiment_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model: WardenProbeModelConfig
    dataset_path: str = Field(min_length=1)
    output_directory: str = Field(min_length=1)
    agent_ids: list[str] = Field(min_length=1)
    capture_targets: list[WardenProbeCaptureTarget] = Field(min_length=1)
    probe: WardenProbeTrainingConfig = Field(default_factory=WardenProbeTrainingConfig)

    @field_validator("agent_ids")
    @classmethod
    def unique_nonblank_agent_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("agent_ids must be unique nonblank identifiers")
        return cleaned

    def config_sha256(self) -> str:
        """Fingerprint the normalized config; this does not fingerprint its data or weights."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

