"""Versioned public contract for message-level interaction graphs (v1.0).

Nodes and edges are only constructed from authorized, explicitly recorded
interaction metadata (WARDEN public run responses or AgentGuard run reports).
A candidate path is a risk hypothesis with references, never a verified leak
path; `verification_status` keeps that distinction machine-readable.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from agentguard.contracts.base import ContractModel
from agentguard.contracts.truth_guard import find_hidden_truth_fields

GraphRiskLevel = Literal["none", "low", "medium", "high", "unknown"]

GraphNodeId = str
GraphEdgeId = str

_MAX_NODES = 500
_MAX_EDGES = 2000
_MAX_PATHS = 50


class GraphNode(ContractModel):
    """An entity observed in a recorded interaction trace."""

    schema_version: Literal["1.0"] = "1.0"
    id: GraphNodeId = Field(min_length=1, max_length=256)
    type: Literal["agent", "tool", "model", "invocation", "external_resource"]
    label: str = Field(min_length=1, max_length=256)
    risk: GraphRiskLevel = "unknown"
    timestamp: str | None = None
    source_id: str = Field(min_length=1, max_length=256)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_hidden_truth_metadata(cls, value: dict[str, str | int | float | bool]) -> dict[str, str | int | float | bool]:
        present = find_hidden_truth_fields(value)
        if present:
            raise ValueError(f"graph node metadata must not contain hidden evaluator fields: {sorted(present)}")
        return value


class GraphEdge(ContractModel):
    """A recorded communication or invocation between two graph nodes."""

    schema_version: Literal["1.0"] = "1.0"
    id: GraphEdgeId = Field(min_length=1, max_length=256)
    source: GraphNodeId = Field(min_length=1, max_length=256)
    target: GraphNodeId = Field(min_length=1, max_length=256)
    type: Literal["message", "invocation", "result"]
    message_id: str | None = Field(default=None, max_length=256)
    risk: GraphRiskLevel = "unknown"
    timestamp: str | None = None
    source_id: str = Field(min_length=1, max_length=256)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_hidden_truth_metadata(cls, value: dict[str, str | int | float | bool]) -> dict[str, str | int | float | bool]:
        present = find_hidden_truth_fields(value)
        if present:
            raise ValueError(f"graph edge metadata must not contain hidden evaluator fields: {sorted(present)}")
        return value


class GraphCandidatePath(ContractModel):
    """An evidence-linked candidate risk path; existence is not a verified leak."""

    schema_version: Literal["1.0"] = "1.0"
    path_id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    node_ids: list[GraphNodeId] = Field(min_length=2, max_length=64)
    edge_ids: list[GraphEdgeId] = Field(min_length=1, max_length=64)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    risk_level: GraphRiskLevel = "unknown"
    rationale: str = Field(min_length=1, max_length=2048)
    verification_status: Literal["unverified", "verified", "unsupported"] = "unverified"


class InteractionGraphResponse(ContractModel):
    """Public message-level graph view for one investigation workflow."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=256)
    available: bool
    data_status: Literal["complete", "partial", "unavailable"]
    source: Literal["warden_observation", "agentguard_synthetic_report", "none"]
    run_id: str | None = Field(default=None, max_length=256)
    scenario_id: str | None = Field(default=None, max_length=256)
    nodes: list[GraphNode] = Field(default_factory=list, max_length=_MAX_NODES)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=_MAX_EDGES)
    candidate_paths: list[GraphCandidatePath] = Field(default_factory=list, max_length=_MAX_PATHS)
    limitations: list[str] = Field(default_factory=list, max_length=64)
