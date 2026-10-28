"""Export public Pydantic contracts to checked-in JSON Schema files."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agentguard.contracts.agent_message import AgentMessage
from agentguard.contracts.artifacts import ArtifactMetadata, ArtifactRef
from agentguard.contracts.attribution import (
    AttributionNodeAnalysisReport,
    AttributionNodeAnalysisRequest,
    PathShiftAnalysisReport,
    PathShiftAnalysisRequest,
)
from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.contracts.defense import (
    DefenseCandidate,
    DefenseLifecycleRecord,
    DefensePreview,
    DefenseStatusEvent,
    DefenseVerification,
)
from agentguard.contracts.evaluation import EvaluationReport
from agentguard.contracts.evidence import CausalValidationCriteria, EvidenceRecord, GovernedEvidence
from agentguard.contracts.experience import (
    ExperienceRecommendationPolicy,
    ExperienceRecord,
    ExperienceSearchResult,
)
from agentguard.contracts.experience_bandit import (
    ExperienceBanditActionScore,
    ExperienceBanditRecommendationReport,
)
from agentguard.contracts.experiment import ExperimentBudget, ExperimentRequest
from agentguard.contracts.graph import (
    CandidatePath,
    InteractionEdge,
    InteractionGraphReport,
    InteractionNode,
)
from agentguard.contracts.hypothesis import (
    InvestigationAction,
    InvestigationHypothesis,
    PairedExperimentDesign,
)
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.lifecycle import BudgetUsage, RunTimelineEvent
from agentguard.contracts.observation import DetectionFinding, Observation
from agentguard.contracts.paired import (
    PairedExperimentExecutionReport,
    PairedExperimentPairResult,
    PairedExperimentRunRequest,
)
from agentguard.contracts.probe import (
    BinaryProbePredictionReport,
    BinaryProbePredictRequest,
    BinaryProbeTrainingReport,
    BinaryProbeTrainRequest,
)
from agentguard.contracts.report import RunReport
from agentguard.contracts.research import (
    OutcomePairs,
    ProbeEffectAssociationInput,
    RegisteredStudy,
    ResearchArmDefinition,
    StudyAnalysisReport,
    StudyAnalysisRequest,
    StudyArmAnalysisInput,
    StudyRegistrationRequest,
)
from agentguard.contracts.research_capability import (
    ResearchCapabilityItem,
    ResearchCapabilityReport,
)
from agentguard.contracts.scenario import ScenarioSpec
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.contracts.warden_probe import WardenWhiteboxProbeExperimentConfig
from agentguard.contracts.whitebox import ActivationPatchSpec

SCHEMAS = {
    "scenario.schema.json": ScenarioSpec,
    "experiment.schema.json": ExperimentRequest,
    "experiment-budget.schema.json": ExperimentBudget,
    "observation.schema.json": Observation,
    "finding.schema.json": DetectionFinding,
    "intervention-request.schema.json": InterventionRequest,
    "intervention-result.schema.json": InterventionResult,
    "evaluation.schema.json": EvaluationReport,
    "evidence.schema.json": EvidenceRecord,
    "governed-evidence.schema.json": GovernedEvidence,
    "causal-validation-criteria.schema.json": CausalValidationCriteria,
    "capabilities.schema.json": CapabilityManifest,
    "agent-message.schema.json": AgentMessage,
    "run-report.schema.json": RunReport,
    "investigation-hypothesis.schema.json": InvestigationHypothesis,
    "investigation-action.schema.json": InvestigationAction,
    "paired-experiment-design.schema.json": PairedExperimentDesign,
    "defense-candidate.schema.json": DefenseCandidate,
    "defense-preview.schema.json": DefensePreview,
    "defense-verification.schema.json": DefenseVerification,
    "defense-lifecycle-record.schema.json": DefenseLifecycleRecord,
    "defense-status-event.schema.json": DefenseStatusEvent,
    "experience-record.schema.json": ExperienceRecord,
    "experience-search-result.schema.json": ExperienceSearchResult,
    "experience-recommendation-policy.schema.json": ExperienceRecommendationPolicy,
    "experience-bandit-action-score.schema.json": ExperienceBanditActionScore,
    "experience-bandit-recommendation-report.schema.json": ExperienceBanditRecommendationReport,
    "artifact-ref.schema.json": ArtifactRef,
    "artifact-metadata.schema.json": ArtifactMetadata,
    "run-timeline-event.schema.json": RunTimelineEvent,
    "budget-usage.schema.json": BudgetUsage,
    "interaction-node.schema.json": InteractionNode,
    "interaction-edge.schema.json": InteractionEdge,
    "candidate-path.schema.json": CandidatePath,
    "interaction-graph-report.schema.json": InteractionGraphReport,
    "binary-probe-train-request.schema.json": BinaryProbeTrainRequest,
    "binary-probe-training-report.schema.json": BinaryProbeTrainingReport,
    "binary-probe-predict-request.schema.json": BinaryProbePredictRequest,
    "binary-probe-prediction-report.schema.json": BinaryProbePredictionReport,
    "paired-experiment-run-request.schema.json": PairedExperimentRunRequest,
    "paired-experiment-pair-result.schema.json": PairedExperimentPairResult,
    "paired-experiment-execution-report.schema.json": PairedExperimentExecutionReport,
    "study-registration-request.schema.json": StudyRegistrationRequest,
    "registered-study.schema.json": RegisteredStudy,
    "study-outcome-pairs.schema.json": OutcomePairs,
    "study-analysis-request.schema.json": StudyAnalysisRequest,
    "study-analysis-report.schema.json": StudyAnalysisReport,
    "research-arm-definition.schema.json": ResearchArmDefinition,
    "study-arm-analysis-input.schema.json": StudyArmAnalysisInput,
    "probe-effect-association-input.schema.json": ProbeEffectAssociationInput,
    "research-capability-item.schema.json": ResearchCapabilityItem,
    "research-capability-report.schema.json": ResearchCapabilityReport,
    "attribution-node-analysis-request.schema.json": AttributionNodeAnalysisRequest,
    "attribution-node-analysis-report.schema.json": AttributionNodeAnalysisReport,
    "path-shift-analysis-request.schema.json": PathShiftAnalysisRequest,
    "path-shift-analysis-report.schema.json": PathShiftAnalysisReport,
    "activation-patch-spec.schema.json": ActivationPatchSpec,
    "warden-audit-request.schema.json": WardenAuditRequest,
    "warden-whitebox-probe-experiment.schema.json": WardenWhiteboxProbeExperimentConfig,
}

def main() -> None:
    destination = PROJECT_ROOT / "schemas"
    destination.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMAS.items():
        payload = model.model_json_schema()
        (destination / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(f"Exported {len(SCHEMAS)} JSON Schemas to {destination}")

if __name__ == "__main__":
    main()
