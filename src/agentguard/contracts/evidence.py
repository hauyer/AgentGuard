from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import Field
from agentguard.contracts.base import ContractModel


class EvidenceRecord(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    evidence_id: str = Field(default_factory=lambda: f"ev-{uuid4().hex[:12]}")
    kind: Literal["observation", "finding", "hypothesis", "experiment_design", "intervention", "defense", "evaluation", "note"]
    grade: Literal[
        "observational", "synthetic", "interventional", "causal_validated"
    ]
    source: str
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    run_id: str | None = None
    experiment_id: str | None = None
    scenario_id: str | None = None
    source_version: str = "unknown"
    conclusion_scope: str = "supports_only_the_summary_and_linked_artifacts"
    access_level: Literal["public", "agent_private", "research_only", "restricted"] = "research_only"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
