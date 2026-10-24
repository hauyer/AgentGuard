from typing import Literal

from pydantic import Field

from agentguard.contracts.agent_message import AgentMessage
from agentguard.contracts.base import ContractModel
from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.contracts.defense import DefenseLifecycleRecord, DefensePreview, DefenseVerification
from agentguard.contracts.evaluation import EvaluationReport
from agentguard.contracts.evidence import EvidenceRecord
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.graph import InteractionGraphReport
from agentguard.contracts.hypothesis import (
    InvestigationAction,
    InvestigationHypothesis,
    PairedExperimentDesign,
)
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.lifecycle import BudgetUsage, RunTimelineEvent
from agentguard.contracts.observation import DetectionFinding, Observation
from agentguard.contracts.paired import PairedExperimentExecutionReport


class RunReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    run_id: str
    experiment_id: str
    scenario_id: str
    status: Literal["completed", "partial", "failed"]
    backend_mode: Literal["synthetic", "real"]
    policy_profile: Literal["isolated_research", "synthetic_demo", "read_only"] = "isolated_research"
    request_snapshot: ExperimentRequest | None = None
    observations: list[Observation] = Field(default_factory=list)
    graph_analysis: InteractionGraphReport | None = None
    detector_layers: list[str] = Field(default_factory=list)
    findings: list[DetectionFinding] = Field(default_factory=list)
    hypotheses: list[InvestigationHypothesis] = Field(default_factory=list)
    investigation_actions: list[InvestigationAction] = Field(default_factory=list)
    experiment_designs: list[PairedExperimentDesign] = Field(default_factory=list)
    defense_preview: DefensePreview | None = None
    defense_verification: DefenseVerification | None = None
    defense_lifecycle: list[DefenseLifecycleRecord] = Field(default_factory=list)
    selected_intervention: InterventionRequest | None = None
    intervention_result: InterventionResult | None = None
    intervention_requests: list[InterventionRequest] = Field(default_factory=list)
    intervention_history: list[InterventionResult] = Field(default_factory=list)
    paired_experiments: list[PairedExperimentExecutionReport] = Field(default_factory=list)
    evaluation: EvaluationReport | None = None
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    capability_manifest: CapabilityManifest | None = None
    timeline: list[RunTimelineEvent] = Field(default_factory=list)
    budget_usage: BudgetUsage | None = None
    agent_messages: list[AgentMessage] = Field(default_factory=list)
    causal_claim_status: Literal[
        "not_assessed", "simulated_only", "empirical_intervention_observed",
        "causal_validation_required"
    ] = "not_assessed"
    limitations: list[str] = Field(default_factory=list)
