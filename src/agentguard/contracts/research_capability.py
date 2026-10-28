"""Read-only, evidence-based readiness for proposed WARDEN research abilities."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel

ResearchCapabilityName = Literal[
    "research_dataset_mechanism",
    "sender_receiver_baseline",
    "agent_isolation",
    "message_block",
    "message_replace",
    "leace_fit_apply",
    "activation_patch",
    "residual_edge_patch",
    "probe_training",
    "persistent_run",
    "artifact_content_read",
]
ResearchCapabilityState = Literal[
    "SUPPORTED_AND_READY",
    "SUPPORTED_BUT_BLOCKED",
    "UNSUPPORTED",
    "UNAVAILABLE",
    "UNKNOWN",
]


class ResearchCapabilityItem(ContractModel):
    capability: ResearchCapabilityName
    status: ResearchCapabilityState
    blockers: list[str] = Field(default_factory=list, max_length=16)
    source_version: str | None = Field(default=None, max_length=128)
    advertised_by_warden: bool | None = None
    observed_at: str = Field(min_length=1)


class ResearchCapabilityReport(ContractModel):
    """A status projection; support is never inferred from API health alone."""

    schema_version: Literal["1.0"] = "1.0"
    backend_status: Literal["available", "unavailable", "blocked", "unknown", "not_configured"]
    source: str = "agentguard:warden-research-capability-check"
    source_version: str | None = Field(default=None, max_length=128)
    source_api_version: str | None = Field(default=None, max_length=64)
    observed_at: str = Field(min_length=1)
    capabilities: list[ResearchCapabilityItem] = Field(min_length=1, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=16)

