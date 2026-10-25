"""Explainable risk findings and hypotheses over real interaction metadata.

Detectors consume the InteractionGraphService projection (recorded
sender/recipient/tool edges only). With metadata-only observations no detector
may claim message-content secrets; unsupported detections are reported as
``insufficient_evidence`` findings instead of being dropped silently. Every
candidate finding is paired with a falsifiable hypothesis whose verification
requires explicitly listed WARDEN capabilities.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from agentguard.contracts.interaction_graph import InteractionGraphResponse
from agentguard.contracts.risk_findings import (
    RiskAnalysisReport,
    RiskFinding,
    RiskHypothesis,
)
from agentguard.services.interaction_graph import InteractionGraphService

_FAN_IN_THRESHOLD = 3


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


class RiskFindingService:
    """Structural, explainable detectors over one investigation's graph."""

    def __init__(self, graphs: InteractionGraphService | None = None) -> None:
        self.graphs = graphs or InteractionGraphService()

    def analyze_workflow(self, entry: Mapping[str, Any]) -> RiskAnalysisReport:
        graph = self.graphs.for_warden_workflow(entry)
        raw_rounds = self._raw_round_sequence(entry)
        return self.analyze_graph(graph, raw_rounds=raw_rounds)

    def analyze_graph(
        self, graph: InteractionGraphResponse,
        *, raw_rounds: list[tuple[int, str]] | None = None,
    ) -> RiskAnalysisReport:
        investigation_id = graph.request_id
        limitations: list[str] = [
            (
                "Findings are structural signals over recorded communication "
                "metadata; message bodies were not inspected and no content-level "
                "secret is claimed."
            ),
        ]
        # Upstream dedup/collapse notes stay visible on the risk report.
        limitations.extend(
            note for note in graph.limitations
            if "duplicate" in note.lower() or "collapsed" in note.lower()
        )
        if not graph.available or not graph.edges:
            limitations.append("No usable interaction metadata; no findings were inferred.")
            return RiskAnalysisReport(
                investigation_id=investigation_id, run_id=graph.run_id,
                available=False, limitations=limitations,
                data_source="none", evidence_level="unknown",
            )

        findings: list[RiskFinding] = []
        findings.extend(self._fan_in_concentration(graph, investigation_id))
        findings.extend(self._relay_chains(graph, investigation_id))
        findings.extend(self._temporal_order(graph, investigation_id, raw_rounds))
        findings.extend(self._external_flow(graph, investigation_id))

        hypotheses: list[RiskHypothesis] = []
        for finding in findings:
            if finding.status == "insufficient_evidence":
                continue
            hypotheses.append(self._hypothesis_for(finding, graph))

        if all(f.status == "insufficient_evidence" for f in findings):
            limitations.append(
                "Current metadata supports no structural detection; recorded "
                "as insufficient evidence rather than a fabricated risk."
            )
        evidence_level = (
            "observational" if graph.source == "warden_observation" else "synthetic"
        )
        for finding in findings:
            finding.evidence_level = evidence_level  # type: ignore[assignment]
        self._mount_graph_anchors(graph, findings)
        return RiskAnalysisReport(
            investigation_id=investigation_id, run_id=graph.run_id,
            available=True, findings=findings, hypotheses=hypotheses,
            limitations=limitations,
            data_source=(
                "warden_observation"
                if graph.source == "warden_observation" else "none"
            ),
            evidence_level=evidence_level,
        )

    @staticmethod
    def _mount_graph_anchors(
        graph: InteractionGraphResponse, findings: list[RiskFinding],
    ) -> None:
        """Attach the graph nodes/edges each finding's evidence points at."""
        node_types = {node.id: node.type for node in graph.nodes}
        for finding in findings:
            targets = {finding.target_ref}
            for ref in finding.supporting_evidence_refs:
                if ref.startswith("edge:"):
                    finding.graph_edge_refs.append(ref)
                    for edge in graph.edges:
                        if edge.id == ref:
                            targets.update({edge.source, edge.target})
                elif ref in node_types:
                    finding.graph_node_refs.append(ref)
            for target in targets:
                if target in node_types:
                    finding.graph_node_refs.append(target)
            finding.graph_edge_refs = list(dict.fromkeys(finding.graph_edge_refs))
            finding.graph_node_refs = list(dict.fromkeys(finding.graph_node_refs))[:64]
            if finding.status == "insufficient_evidence":
                finding.severity = "unknown"
            elif finding.risk_type in {"relay_path_candidate", "external_data_flow"}:
                finding.severity = "medium"
            elif finding.risk_type == "collaboration_concentration":
                finding.severity = "low"

    @staticmethod
    def _raw_round_sequence(entry: Mapping[str, Any]) -> list[tuple[int, str]]:
        """Round/message pairs in recorded order, before graph sorting."""
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        run = context.get("run") if isinstance(context.get("run"), Mapping) else {}
        evidence = run.get("evidence") if isinstance(run.get("evidence"), Mapping) else {}
        rounds: list[tuple[int, str]] = []
        for finding in evidence.get("findings") or []:
            if not isinstance(finding, Mapping):
                continue
            for message in finding.get("messages") or []:
                if not isinstance(message, Mapping):
                    continue
                round_index = message.get("round_index")
                message_id = message.get("message_id")
                if isinstance(round_index, int) and not isinstance(round_index, bool) \
                        and isinstance(message_id, str):
                    rounds.append((round_index, message_id))
        return rounds

    # ------------------------------------------------------------------ #
    # detectors
    # ------------------------------------------------------------------ #
    def _fan_in_concentration(
        self, graph: InteractionGraphResponse, investigation_id: str
    ) -> list[RiskFinding]:
        senders: dict[str, set[str]] = defaultdict(set)
        edge_ids: dict[str, list[str]] = defaultdict(list)
        for edge in graph.edges:
            if edge.type != "message":
                continue
            senders[edge.target].add(edge.source)
            edge_ids[edge.target].append(edge.id)
        findings: list[RiskFinding] = []
        for target, sources in senders.items():
            if len(sources) < _FAN_IN_THRESHOLD:
                continue
            findings.append(RiskFinding(
                finding_id=_stable_id("rf-fanin", investigation_id, target),
                investigation_id=investigation_id,
                risk_type="collaboration_concentration",
                detection_method="collaboration_concentration",
                description=(
                    f"{len(sources)} distinct agents send messages to {target} "
                    f"({', '.join(sorted(sources))}). Concentration is a structural "
                    "signal; it may be this scenario's normal review design and is "
                    "not evidence of collusion or leakage."
                ),
                target_ref=target,
                supporting_evidence_refs=edge_ids[target],
                limitations=[
                    (
                        "Single-run observation without a baseline; concentration may "
                        "match the scenario's designed review topology."
                    ),
                ],
            ))
        return findings

    def _relay_chains(
        self, graph: InteractionGraphResponse, investigation_id: str
    ) -> list[RiskFinding]:
        outgoing: dict[str, list[Any]] = defaultdict(list)
        incoming: dict[str, list[Any]] = defaultdict(list)
        for edge in graph.edges:
            if edge.type != "message":
                continue
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)
        findings: list[RiskFinding] = []
        for relay in sorted(set(outgoing) & set(incoming)):
            for in_edge in incoming[relay]:
                for out_edge in outgoing[relay]:
                    if out_edge.target == in_edge.source:
                        continue
                    findings.append(RiskFinding(
                        finding_id=_stable_id(
                            "rf-relay", investigation_id, in_edge.id, out_edge.id,
                        ),
                        investigation_id=investigation_id,
                        risk_type="relay_path_candidate",
                        detection_method="relay_path_candidate",
                        description=(
                            f"Message path {in_edge.source} -> {relay} -> {out_edge.target} "
                            f"({in_edge.id}, {out_edge.id}) shows relay behaviour. The relay "
                            "may be forwarding authorized coordination; content was not read."
                        ),
                        target_ref=relay,
                        supporting_evidence_refs=[in_edge.id, out_edge.id],
                    limitations=[
                        (
                            "Relay structure alone does not establish unauthorized "
                            "information flow."
                        ),
                    ],
                    ))
        return findings

    def _temporal_order(
        self,
        graph: InteractionGraphResponse,
        investigation_id: str,
        raw_rounds: list[tuple[int, str]] | None,
    ) -> list[RiskFinding]:
        if not raw_rounds:
            return [RiskFinding(
                finding_id=_stable_id("rf-order", investigation_id, "order"),
                investigation_id=investigation_id,
                risk_type="temporal_order",
                detection_method="temporal_order_anomaly",
                description=(
                    "Interaction metadata carries no per-message round indices "
                    "in record order, so temporal-order analysis cannot be "
                    "performed. Graph edges are round-sorted and cannot reveal "
                    "recording-order anomalies."
                ),
                target_ref=investigation_id,
                status="insufficient_evidence",
                calibration="not_applicable",
                supporting_evidence_refs=[],
            )]
        findings: list[RiskFinding] = []
        highest = None
        for round_index, message_id in raw_rounds:
            if highest is not None and round_index < highest:
                findings.append(RiskFinding(
                    finding_id=_stable_id("rf-order", investigation_id, message_id),
                    investigation_id=investigation_id,
                    risk_type="temporal_order",
                    detection_method="temporal_order_anomaly",
                    description=(
                        f"Message {message_id} carries round_index {round_index} after "
                        f"a higher round {highest} in record order; ordering may be "
                        "inconsistent or metadata may be misrecorded."
                    ),
                    target_ref=f"edge:{message_id}",
                    supporting_evidence_refs=[f"edge:{message_id}"],
                    limitations=[
                        "Round indices are metadata; clock-level ordering was not observed.",
                    ],
                ))
            highest = max(highest or 0, round_index)
        return findings

    def _external_flow(
        self, graph: InteractionGraphResponse, investigation_id: str
    ) -> list[RiskFinding]:
        node_types = {node.id: node.type for node in graph.nodes}
        external = {nid for nid, kind in node_types.items() if kind == "external_resource"}
        tools = {nid for nid, kind in node_types.items() if kind == "tool"}
        if not external or not tools:
            return [RiskFinding(
                finding_id=_stable_id("rf-extflow", investigation_id, "ext"),
                investigation_id=investigation_id,
                risk_type="external_data_flow",
                detection_method="external_flow_candidate",
                description=(
                    "No external-resource and tool nodes coexist in this "
                    "observation, so an external-resource-to-tool flow check is "
                    "not supported by the current data."
                ),
                target_ref=investigation_id,
                status="insufficient_evidence",
                calibration="not_applicable",
                supporting_evidence_refs=[],
            )]
        findings: list[RiskFinding] = []
        for edge in graph.edges:
            if edge.source in external and edge.target in tools:
                findings.append(RiskFinding(
                    finding_id=_stable_id("rf-extflow", investigation_id, edge.id),
                    investigation_id=investigation_id,
                    risk_type="external_data_flow",
                    detection_method="external_flow_candidate",
                    description=(
                        f"Edge {edge.id} connects external resource {edge.source} "
                        f"to tool {edge.target}; a candidate unauthorized flow path."
                    ),
                    target_ref=edge.id,
                    supporting_evidence_refs=[edge.id, *edge.evidence_refs],
                    limitations=["Path existence is not flow confirmation."],
                ))
        return findings

    # ------------------------------------------------------------------ #
    # hypothesis generation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _hypothesis_for(finding: RiskFinding, graph: InteractionGraphResponse) -> RiskHypothesis:
        method_texts = {
            "collaboration_concentration": (
                f"The concentration of senders onto {finding.target_ref} is associated "
                "with the recorded interaction pattern and may participate in "
                "risk-relevant coordination."
            ),
            "relay_path_candidate": (
                f"Agent {finding.target_ref} acting as a relay is associated with the "
                "recorded message path and may mediate information transfer between "
                "its neighbours."
            ),
            "temporal_order_anomaly": (
                "The recorded round ordering anomaly reflects either genuine out-of-"
                "order behaviour or metadata corruption."
            ),
            "external_flow_candidate": (
                "The external-resource-to-tool path is associated with candidate "
                "unauthorized data flow."
            ),
            "tool_use_pattern_candidate": (
                "The observed tool-usage pattern is associated with candidate "
                "policy-inconsistent behaviour."
            ),
        }
        return RiskHypothesis(
            hypothesis_id=f"rh-{finding.finding_id[3:]}",
            investigation_id=finding.investigation_id,
            finding_id=finding.finding_id,
            statement=(
                method_texts.get(finding.detection_method, finding.description)
                + " This association is observational and unverified."
            ),
            expected_effect=(
                "If the association is causal, a matched replay that blocks or perturbs "
                "the implicated communication would change the recorded risk signal; "
                "no such replay has been run."
            ),
            supporting_evidence=finding.supporting_evidence_refs,
            contradicting_evidence=finding.contradicting_evidence_refs,
            next_check=(
                "capture_only audit on the implicated agents to gather activation-level "
                "evidence, or a matched-pair replay when the kernel supports it"
            ),
            required_capabilities=["activation.backend", "paired_replay"],
            intervention_supported=False,
            stop_condition=(
                "Reject or shelve the hypothesis when two matched replays show no "
                "difference in the recorded signal, or when a stronger baseline shows "
                "the pattern is the scenario's normal topology."
            ),
        )
