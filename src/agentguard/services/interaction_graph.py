"""Message-level interaction graph construction from authorized run metadata.

Only two provenance-clear sources are consumed:

1. ``warden_observation`` — message-edge metadata that WARDEN's public run
   response explicitly provided and the workflow journal recorded
   (``run.evidence.findings[].messages`` with sender/recipient/message_id).
2. ``agentguard_synthetic_report`` — Observation events of the local synthetic
   research engine. These are always labelled synthetic and never presented as
   a real interaction trace.

Edges are never inferred from risk scores, task success, or any other proxy.
Missing metadata degrades to ``partial`` or ``unavailable``; it is never filled
in. References are validated with the EvidenceGateway safety rule so local
paths, ``file://`` URIs and credential-bearing URLs are dropped unopened.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentguard.contracts.interaction_graph import (
    GraphEdge,
    GraphNode,
    GraphRiskLevel,
    InteractionGraphResponse,
)
from agentguard.contracts.report import RunReport
from agentguard.services.evidence_gateway import reference_is_safe

_MAX_NODES = 500
_MAX_EDGES = 2000
_SYNTHETIC_NOTE = (
    "Graph edges come from AgentGuard's synthetic research engine; they are "
    "scripted demo events, not a real agent interaction trace."
)
_NO_SOURCE_NOTE = (
    "GRAPH_SOURCE_UNAVAILABLE: no authorized message-level metadata is "
    "recorded for this workflow; no edges were constructed."
)

_SEVERITY_TO_RISK: dict[str, GraphRiskLevel] = {
    "low": "low", "medium": "medium", "high": "high", "critical": "high",
}
_RISK_ORDER = {"unknown": 0, "none": 1, "low": 2, "medium": 3, "high": 4}


def _max_risk(*levels: GraphRiskLevel) -> GraphRiskLevel:
    return max(levels, key=lambda level: _RISK_ORDER[level])


def _safe_refs(refs: Sequence[Any]) -> list[str]:
    return [ref for ref in refs if isinstance(ref, str) and reference_is_safe(ref)]


def _clean_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 256 or any(ord(char) < 32 for char in cleaned):
        return None
    return cleaned


class InteractionGraphService:
    """Project recorded interaction metadata into the public graph contract."""

    def for_warden_workflow(self, entry: Mapping[str, Any]) -> InteractionGraphResponse:
        """Build the graph from the WARDEN run payload recorded in the journal."""
        request_id = str(entry.get("request_id") or "")
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        result = entry.get("result") if isinstance(entry.get("result"), Mapping) else {}
        request = entry.get("request") if isinstance(entry.get("request"), Mapping) else {}
        run = context.get("run") if isinstance(context.get("run"), Mapping) else None
        if run is None and isinstance(result.get("run"), Mapping):
            run = result["run"]
        scenario_id = _clean_identifier(request.get("scenario_id"))
        run_id = entry.get("run_id") or (run or {}).get("run_id")
        run_id = _clean_identifier(run_id)
        limitations: list[str] = []

        if run is None:
            return InteractionGraphResponse(
                request_id=request_id, available=False, data_status="unavailable",
                source="none", run_id=run_id, scenario_id=scenario_id,
                limitations=[
                    _NO_SOURCE_NOTE,
                    (
                        f"Workflow journal status is {entry.get('status', 'unknown')}; "
                        "no WARDEN run payload with message metadata is recorded."
                    ),
                ],
            )

        evidence = run.get("evidence") if isinstance(run.get("evidence"), Mapping) else {}
        findings = evidence.get("findings") if isinstance(evidence.get("findings"), list) else []
        artifacts = evidence.get("artifacts") if isinstance(evidence.get("artifacts"), list) else []
        run_refs = _safe_refs(
            item.get("artifact_id") for item in artifacts if isinstance(item, Mapping)
        )

        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        seen_edge_keys: dict[str, tuple[str, str]] = {}
        skipped = 0
        duplicates = 0

        for finding in findings:
            if not isinstance(finding, Mapping):
                skipped += 1
                continue
            messages = finding.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, Mapping):
                    skipped += 1
                    continue
                sender = _clean_identifier(message.get("sender"))
                recipient = _clean_identifier(message.get("recipient"))
                message_id = _clean_identifier(message.get("message_id"))
                if not sender or not recipient or not message_id:
                    skipped += 1
                    continue
                edge_key = message_id
                endpoints = (f"agent:{sender}", f"agent:{recipient}")
                if edge_key in seen_edge_keys:
                    if seen_edge_keys[edge_key] != endpoints:
                        # Same message id reused for different endpoints is a data
                        # conflict; keep the first record and flag the drop.
                        skipped += 1
                        continue
                    duplicates += 1
                    continue
                seen_edge_keys[edge_key] = endpoints
                round_index = message.get("round_index")
                metadata: dict[str, str | int | float | bool] = {}
                if isinstance(round_index, int) and not isinstance(round_index, bool):
                    metadata["round_index"] = round_index
                for participant in endpoints:
                    if participant not in nodes:
                        nodes[participant] = GraphNode(
                            id=participant, type="agent",
                            label=participant.removeprefix("agent:"),
                            source_id=message_id,
                        )
                edges.append(GraphEdge(
                    id=f"edge:{message_id}", source=endpoints[0], target=endpoints[1],
                    type="message", message_id=message_id, source_id=message_id,
                    evidence_refs=list(run_refs), metadata=metadata,
                ))

        if skipped:
            limitations.append(
                f"{skipped} recorded message entries lacked sender, recipient or "
                "message_id (or conflicted with an earlier entry) and were dropped."
            )
        if duplicates:
            limitations.append(
                f"{duplicates} duplicate message entries were collapsed; edge ids stay unique."
            )
        limitations.append(
            "The source provides message-level sender/recipient metadata without "
            "per-message timestamps; ordering uses round_index where present."
        )
        if not run_refs:
            limitations.append(
                "No safe artifact reference was available from the run payload; "
                "edges carry no evidence_refs."
            )
        limitations.append(
            "Edges represent recorded communication metadata only; they are not "
            "evidence of secret leakage, and no candidate path is inferred from "
            "risk scores."
        )

        if not edges:
            return InteractionGraphResponse(
                request_id=request_id, available=False, data_status="unavailable",
                source="none", run_id=run_id, scenario_id=scenario_id,
                limitations=[_NO_SOURCE_NOTE, *limitations],
            )

        edges.sort(key=lambda edge: (
            edge.metadata.get("round_index", 0) if isinstance(edge.metadata.get("round_index"), int) else 0,
            edge.id,
        ))
        truncated_nodes = list(nodes.values())[:_MAX_NODES]
        if len(nodes) > _MAX_NODES:
            limitations.append(f"Node list truncated to {_MAX_NODES}.")
        allowed = {node.id for node in truncated_nodes}
        kept_edges = [edge for edge in edges if edge.source in allowed and edge.target in allowed][:_MAX_EDGES]
        if len(edges) > _MAX_EDGES:
            limitations.append(f"Edge list truncated to {_MAX_EDGES}.")
        partial = bool(skipped or duplicates or len(kept_edges) != len(edges))
        return InteractionGraphResponse(
            request_id=request_id, available=True,
            data_status="partial" if partial else "complete",
            source="warden_observation", run_id=run_id, scenario_id=scenario_id,
            nodes=truncated_nodes, edges=kept_edges, candidate_paths=[],
            limitations=limitations,
        )

    def for_run_report(self, report: RunReport, *, request_id: str) -> InteractionGraphResponse:
        """Build a clearly-synthetic graph from local engine observation events."""
        limitations: list[str] = [_SYNTHETIC_NOTE]
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        edge_risk: dict[str, GraphRiskLevel] = {}
        edge_refs: dict[str, list[str]] = {}

        finding_by_target: dict[str, list[tuple[GraphRiskLevel, list[str]]]] = {}
        for finding in report.findings:
            risk = _SEVERITY_TO_RISK.get(finding.severity, "unknown")
            finding_by_target.setdefault(finding.target_ref, []).append(
                (risk, _safe_refs(finding.evidence_refs))
            )

        skipped = 0
        for observation in report.observations:
            if observation.event_type not in {"message", "tool_call", "tool_result"}:
                continue
            if observation.visibility == "agent_private":
                # Private agent state is not exposed through the public graph view.
                skipped += 1
                continue
            source_id = observation.source or observation.actor_id
            target_id = observation.target
            if not _clean_identifier(source_id) or not _clean_identifier(target_id):
                skipped += 1
                continue
            event_type = observation.event_type
            source_node = self._participant_node(
                nodes, source_id, "tool" if event_type == "tool_result" else "agent",
                observation.event_id,
            )
            target_node = self._participant_node(
                nodes, target_id, "tool" if event_type == "tool_call" else "agent",
                observation.event_id,
            )
            edge_type = {"message": "message", "tool_call": "invocation", "tool_result": "result"}[event_type]
            edge_id = f"edge:{observation.event_id}"
            risk: GraphRiskLevel = "unknown"
            refs: list[str] = []
            for level, finding_refs in finding_by_target.get(observation.target_ref, []):
                risk = _max_risk(risk, level)
                refs.extend(finding_refs)
            edges.append(GraphEdge(
                id=edge_id, source=source_node.id, target=target_node.id, type=edge_type,
                message_id=_clean_identifier(observation.target_ref),
                risk=risk, timestamp=observation.timestamp.isoformat(),
                source_id=observation.event_id, evidence_refs=list(dict.fromkeys(refs)),
                metadata={"event_type": event_type},
            ))
            if risk != "unknown":
                edge_risk[edge_id] = risk
                edge_refs[edge_id] = list(dict.fromkeys(refs))

        if skipped:
            limitations.append(
                f"{skipped} observation events were excluded (agent-private visibility "
                "or missing source/target identifiers)."
            )
        limitations.append(
            "Edge risk levels reflect detector findings on matching targets and are "
            "uncalibrated screening signals, not confirmed incidents."
        )
        limitations.append(
            "Candidate paths are not synthesized for synthetic reports; no path is "
            "invented without recorded path evidence."
        )

        for edge in edges:
            risk = edge_risk.get(edge.id, "unknown")
            if risk == "unknown":
                continue
            for node_id in (edge.source, edge.target):
                node = nodes[node_id]
                node.risk = _max_risk(node.risk, risk)

        if not edges:
            return InteractionGraphResponse(
                request_id=request_id, available=False, data_status="unavailable",
                source="none", run_id=report.run_id, scenario_id=report.scenario_id,
                limitations=[
                    (
                        "GRAPH_SOURCE_UNAVAILABLE: this run recorded no usable "
                        "message, tool_call or tool_result observations."
                    ),
                    *limitations,
                ],
            )
        truncated_nodes = list(nodes.values())[:_MAX_NODES]
        if len(nodes) > _MAX_NODES:
            limitations.append(f"Node list truncated to {_MAX_NODES}.")
        allowed = {node.id for node in truncated_nodes}
        kept_edges = [edge for edge in edges if edge.source in allowed and edge.target in allowed][:_MAX_EDGES]
        if len(edges) > _MAX_EDGES:
            limitations.append(f"Edge list truncated to {_MAX_EDGES}.")
        return InteractionGraphResponse(
            request_id=request_id, available=True,
            data_status="partial" if skipped or len(kept_edges) != len(edges) else "complete",
            source="agentguard_synthetic_report", run_id=report.run_id,
            scenario_id=report.scenario_id, nodes=truncated_nodes, edges=kept_edges,
            candidate_paths=[], limitations=limitations,
        )

    @staticmethod
    def _participant_node(
        nodes: dict[str, GraphNode], participant: str, node_type: str, source_id: str
    ) -> GraphNode:
        node_id = f"{node_type}:{participant}"
        if node_id not in nodes:
            nodes[node_id] = GraphNode(
                id=node_id, type=node_type, label=participant, source_id=source_id,
            )
        return nodes[node_id]
