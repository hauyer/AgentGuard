"""四层决策的严格输入合同；政策等级不代表概率。"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=192, pattern=r"^[A-Za-z0-9_.:>\-]+$")]
Level = Annotated[int, Field(strict=True, ge=1, le=5)]
Unit = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeSecurityLevel(StrictModel):
    agent_id: Identifier
    protection_level: Level | None = None


class ChannelSecurityLevel(StrictModel):
    edge_id: Identifier
    assurance_level: Level | None = None


class OptimizationPolicy(StrictModel):
    threat_model: Literal["fixed_global_inversion"] = "fixed_global_inversion"
    minimum_families: int = Field(strict=True, ge=30, le=100000)
    forward_pass_reference: float = Field(gt=0, allow_inf_nan=False)
    disruption_by_arm: dict[Identifier, Unit]
    permitted_arms: list[Identifier] = Field(min_length=1)
    risk_weight: Unit = .5
    utility_weight: Unit = .3
    cost_weight: Unit = .1
    disruption_weight: Unit = .1


class SecurityProfile(StrictModel):
    schema_version: Literal["gsei.security-profile/1"] = "gsei.security-profile/1"
    profile_id: Identifier
    study_id: Identifier
    workflow_id: Identifier
    policy_version: Identifier
    source_ref: str = Field(min_length=1, max_length=512)
    nodes: list[NodeSecurityLevel] = Field(max_length=100)
    channels: list[ChannelSecurityLevel] = Field(max_length=200)
    alpha: Unit = .5
    beta: Unit = .3
    gamma: Unit = .2
    threshold: Unit = .5
    optimization_policy: OptimizationPolicy | None = None

    @model_validator(mode="after")
    def valid_policy(self):
        if abs(self.alpha + self.beta + self.gamma - 1) > 1e-9:
            raise ValueError("风险权重之和必须为1")
        if len({n.agent_id for n in self.nodes}) != len(self.nodes) or len({e.edge_id for e in self.channels}) != len(self.channels):
            raise ValueError("等级配置引用不得重复")
        return self


class ValueEvent(StrictModel):
    schema_version: Literal["gsei.value-event/1"] = "gsei.value-event/1"
    event_id: Identifier
    study_id: Identifier
    run_id: Identifier
    target_type: Literal["node", "edge", "path"]
    target_ids: list[Identifier] = Field(min_length=1, max_length=20)
    harm_level: Annotated[int, Field(strict=True, ge=0, le=5)] | None = None
    business_value: Level | None = None
    policy_violation: Annotated[bool, Field(strict=True)] | None = None
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    classification_source: str = Field(min_length=1, max_length=512)


class DecisionPreview(StrictModel):
    workflow_id: Identifier
    profile_artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    batch_id: Identifier | None = None


class DecisionExecution(StrictModel):
    arm_id: Identifier
    paired_batch: bool = False


class RiskAssessment(StrictModel):
    lower: Unit
    upper: Unit
    risk_score: Unit | None
    status: Literal["NEEDS_REVIEW", "BELOW_THRESHOLD", "AT_OR_ABOVE_THRESHOLD"]
    risk_kind: Literal["priority_score_not_probability"] = "priority_score_not_probability"
    policy_sha256: str
    explanation: dict


class InterventionCandidate(StrictModel):
    level: Literal["L1", "L2", "L3", "L4"]
    method_id: Literal["component_ablation", "behavior_steering", "leace", "activation_patch", "agent_isolation", "message_block", "message_replace"]
    arm_id: str | None = None
    feasible: bool
    reasons: list[str]
    execution_contract: dict | None = None
    effect_evidence: dict | None = None
    objective: float | None = Field(default=None, allow_inf_nan=False, ge=0)


class InterventionDecision(StrictModel):
    schema_version: Literal["gsei.decision/1"] = "gsei.decision/1"
    study_id: str
    run_id: str
    event_id: str
    profile_artifact_id: str
    event_artifact_id: str
    risk: RiskAssessment
    candidates: list[InterventionCandidate]
    recommended_arm_id: str | None = None
    decision_status: Literal["NEEDS_REVIEW", "CANDIDATES_ONLY", "OPTIMIZED"]
    execution_status: Literal["NOT_SUBMITTED"] = "NOT_SUBMITTED"
    causal_status: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
