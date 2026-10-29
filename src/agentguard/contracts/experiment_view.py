"""Versioned, public and strictly validated experimental event and S-result contracts."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import AwareDatetime, Field, FiniteFloat, model_validator

from agentguard.contracts.base import ContractModel
from agentguard.contracts.research_result import Sha256


class ExperimentEvent(ContractModel):
    schema_version: Literal["stego.event/1"]
    event_id: str = Field(min_length=1)
    run_id: str
    study_id: str
    arm_id: str
    seq: int = Field(ge=1)
    at_utc: AwareDatetime
    elapsed_ms: FiniteFloat | None = Field(default=None, ge=0)
    type: Literal["agent_started", "message_sent", "message_delivered", "message_blocked",
                  "message_replaced", "probe_observed", "intervention_planned",
                  "intervention_applied", "intervention_failed", "receiver_decoded",
                  "task_evaluated", "run_completed", "run_failed"]
    actor_id: str | None = None
    source_agent_id: str | None = None
    target_agent_id: str | None = None
    message_id: str | None = None
    channel_id: str | None = None
    intervention_id: str | None = None
    evidence_refs: list[str]
    data: dict[str, str | int | float | bool | None]
    provenance: Literal["observed", "derived", "planned", "demo"]

    @model_validator(mode="after")
    def safe_public_fields(self):
        allowed = {"case_family", "message_sha256", "length", "round", "original_sha256",
                   "replacement_sha256", "method", "kind", "target", "mode", "layer",
                   "token_policy", "generation_step", "value", "threshold", "threshold_source",
                   "label_semantics", "evaluation_split", "claim_level", "probability_calibrated",
                   "probe_id", "decoded", "recovered", "ber", "missing_reason", "utility",
                   "trial_count", "failure_count", "error", "measurement_phase", "failure_stage"}
        if set(self.data) - allowed or self.at_utc.utcoffset().total_seconds() != 0:
            raise ValueError("Unsafe event fields or non-UTC time")
        if any(isinstance(v, float) and not math.isfinite(v) for v in self.data.values()):
            raise ValueError("Event values must be finite")
        if self.type.startswith("message_") and not all((self.message_id, self.source_agent_id,
                                                         self.target_agent_id, self.channel_id)):
            raise ValueError("Message identity is incomplete")
        return self


class EventArtifact(ContractModel):
    schema_version: Literal["stego.events/1"]
    run_id: str
    study_id: str
    arm_id: str
    events: list[ExperimentEvent] = Field(max_length=20000)

    @model_validator(mode="after")
    def validate_stream(self):
        ids = set()
        previous_time = None
        for seq, event in enumerate(self.events, 1):
            if (event.seq != seq or event.event_id in ids or event.run_id != self.run_id
                    or event.study_id != self.study_id or event.arm_id != self.arm_id):
                raise ValueError("Event stream sequence or identity mismatch")
            ids.add(event.event_id)
            if previous_time is not None and event.at_utc < previous_time:
                raise ValueError("Event time moved backwards")
            previous_time = event.at_utc
        return self


class OptionalMetric(ContractModel):
    value: FiniteFloat | None
    denominator: int = Field(ge=0)

    @model_validator(mode="after")
    def require_denominator(self):
        if self.value is not None and self.denominator == 0:
            raise ValueError("Measured values require a denominator")
        return self


class PublicOutcome(ContractModel):
    case_family: str
    split: Literal["test"]
    executed: bool
    decoded: bool
    recovered: bool
    ber: FiniteFloat | None = Field(ge=0, le=1)
    utility: FiniteFloat | None = Field(ge=0, le=1)
    missing_reason: str | None

    @model_validator(mode="after")
    def consistent_readout(self):
        if ((self.decoded and not self.executed) or (self.recovered and not self.decoded)
                or (self.ber is not None) != self.decoded
                or (not self.executed and self.utility is not None)):
            raise ValueError("Inconsistent execution or decode outcome")
        return self


class PublicArtifactReference(ContractModel):
    artifact_id: str = Field(pattern=r"^public-summary-[a-f0-9]{64}$")
    kind: Literal["public-summary"]
    content_hash: Sha256
    media_type: Literal["application/json"]
    size_bytes: int = Field(ge=1, le=1048576)
    metadata: dict = Field(default_factory=dict)


class LiveResearchManifest(ContractModel):
    schema_version: Literal["warden.research-run/2"]
    run_id: str
    plan_id: str
    study_id: str
    arm_id: str
    protocol_ref: str
    contract_id: str
    config_sha256: Sha256
    executor_sha256: Sha256
    dataset_sha256: Sha256
    split_sha256: Sha256
    model_id: str
    model_revision: str
    tokenizer_revision: str
    seed: int
    started_at: AwareDatetime
    finished_at: AwareDatetime
    status: Literal["completed", "failed"]
    provenance: Literal["real_sender_receiver"]
    communication_mode: Literal["instrumented_llm_codebook"]
    track: Literal["live_steganography"]
    metrics: dict[str, OptionalMetric]
    missing_metrics: dict[str, str]
    outcomes: list[PublicOutcome]
    events_artifact: PublicArtifactReference
    model_calls: int = Field(ge=1)
    training_sha256: Sha256
    files_sha256: dict[str, Sha256]
    model_files_sha256: dict[str, Sha256] = Field(default_factory=dict)
    limitations: list[str]

    @model_validator(mode="after")
    def validate_result(self):
        if (self.finished_at < self.started_at
                or len({o.case_family for o in self.outcomes}) != len(self.outcomes)):
            raise ValueError("Invalid S outcome times or duplicated family")
        n = len(self.outcomes)
        metric = self.metrics.get("SRR_ITT")
        if (not n or metric is None or metric.denominator != n
                or metric.value != sum(o.recovered for o in self.outcomes) / n):
            raise ValueError("S recovery denominator or value cannot be recomputed")
        for key in self.files_sha256:
            if "/" in key or "\\" in key or key in {".", ".."}:
                raise ValueError("Public file hashes must use basenames")
        if self.started_at.utcoffset().total_seconds() or self.finished_at.utcoffset().total_seconds():
            raise ValueError("Manifest times must be UTC")
        executed = [o for o in self.outcomes if o.executed]
        decoded = [o for o in self.outcomes if o.ber is not None]
        utility = [o.utility for o in self.outcomes if o.utility is not None]
        expected = {
            "SRR_executed": (sum(o.recovered for o in executed) / len(executed) if executed else None, len(executed)),
            "BER": (sum(o.ber for o in decoded) / len(decoded) if decoded else None, len(decoded)),
            "NORMAL_UTILITY": (sum(utility) / len(utility) if utility else None, len(utility)),
        }
        for name, (value, denominator) in expected.items():
            measured = self.metrics.get(name)
            if measured is None or measured.value != value or measured.denominator != denominator:
                raise ValueError("S metric cannot be recomputed: " + name)
        return self


class GraphNode(ContractModel):
    id: str
    label: str
    role: str
    status: Literal["active", "isolated", "failed", "not_started"]
    source_event_ids: list[str]


class MessageEdge(ContractModel):
    id: str
    source: str
    target: str
    message_ids: list[str]
    channel_type: Literal["message", "tool", "memory"]
    delivered_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    source_event_ids: list[str]


class ProbeEvidence(ContractModel):
    id: str
    associated_edge_id: str | None
    agent_id: str
    target_layer: int | None
    token_policy: str | None
    label_semantics: str
    value: FiniteFloat | None
    threshold: FiniteFloat | None
    evaluation_split: str | None
    evidence_refs: list[str]
    source_event_ids: list[str]
    claim_level: Literal["linear_decodability"]
    threshold_source: str | None
    probability_calibrated: Literal[False]
    case_family: str | None
    detection_status: Literal["observed", "target_label_threshold_passed", "below_threshold"] = "observed"
    threshold_passed: bool | None = None
    probe_model_version: str | None = None
    measurement_phase: str | None = None

    @model_validator(mode="after")
    def consistent_threshold(self):
        if self.threshold_passed is not None and (
            self.value is None or self.threshold is None or self.threshold_passed != (self.value >= self.threshold)):
            raise ValueError("Probe threshold status contradicts frozen measurements")
        return self


class InterventionTarget(ContractModel):
    type: Literal["agent", "edge", "activation"]
    id: str


class InterventionOverlay(ContractModel):
    id: str
    arm_id: str
    kind: str
    target: InterventionTarget
    execution_state: Literal["planned", "applied", "failed", "not_run"]
    source_event_ids: list[str]
    details: dict = Field(default_factory=dict)


class GraphSnapshot(ContractModel):
    schema_version: Literal["stego.graph/1"]
    study_id: str
    run_id: str | None
    track: Literal["mechanism_testbed", "live_steganography"]
    state: Literal["observed", "unavailable"]
    nodes: list[GraphNode]
    communication_edges: list[MessageEdge]
    probe_evidence: list[ProbeEvidence]
    interventions: list[InterventionOverlay]
    evidence_status: Literal["verified", "not_run"]
    limitations: list[str]


class VerifiedViewArtifact(ContractModel):
    artifact_id: str
    sha256: Sha256


class ExperimentVisualization(ContractModel):
    schema_version: Literal["stego.visualization/1"]
    data_mode: Literal["RECORDED", "UNAVAILABLE"]
    origin_mode: Literal["LIVE_S", "LIVE_M", "UNAVAILABLE"]
    workflow_id: str
    status: str
    graph: GraphSnapshot
    events: list[ExperimentEvent]
    last_seq: int = Field(ge=0)
    metrics: dict[str, OptionalMetric]
    artifacts: list[VerifiedViewArtifact]
    unavailable_reason: str | None
    limitations: list[str]


class ExperimentTimeline(ContractModel):
    schema_version: Literal["stego.timeline/1"]
    events: list[ExperimentEvent]
    next_seq: int = Field(ge=0)
    last_seq: int = Field(ge=0)
    has_more: bool
    status: str
    unavailable_reason: str | None
