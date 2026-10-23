from typing import Literal
from pydantic import Field
from agentguard.contracts.base import ContractModel


class InteractionNode(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    node_id: str
    node_type: Literal["agent", "tool", "unknown"] = "agent"
    event_count: int = Field(default=0, ge=0)
    labels: list[str] = Field(default_factory=list)


class InteractionEdge(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    edge_id: str
    source: str
    target: str
    event_type: str
    target_ref: str | None = None
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_reasons: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class CandidatePath(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    nodes: list[str] = Field(min_length=2)
    edge_refs: list[str] = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class InteractionGraphReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    nodes: list[InteractionNode] = Field(default_factory=list)
    edges: list[InteractionEdge] = Field(default_factory=list)
    candidate_paths: list[CandidatePath] = Field(default_factory=list)
    node_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    suspicious_edge_count: int = Field(default=0, ge=0)
    limitations: list[str] = Field(default_factory=list)
