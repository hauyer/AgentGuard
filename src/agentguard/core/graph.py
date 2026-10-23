"""Evidence-linked interaction graph construction and candidate path analysis."""
from __future__ import annotations
from collections import defaultdict
from agentguard.contracts.graph import InteractionNode, InteractionEdge, CandidatePath, InteractionGraphReport
from agentguard.contracts.observation import Observation, DetectionFinding


class InteractionGraphAnalyzer:
    """Build a temporal interaction graph from observed event metadata only."""

    def analyze(self, observations: list[Observation]) -> InteractionGraphReport:
        nodes: dict[str, InteractionNode] = {}
        edges: list[InteractionEdge] = []
        for obs in sorted(observations, key=lambda item: item.timestamp):
            source = obs.source or obs.actor_id or "unknown"
            target = obs.target or "unknown"
            for node_id, node_type in ((source, "tool" if "tool" in source.lower() else "agent"),
                                       (target, "tool" if "tool" in target.lower() else "agent")):
                if node_id not in nodes:
                    nodes[node_id] = InteractionNode(node_id=node_id, node_type=node_type, event_count=0)
                nodes[node_id].event_count += 1
            features = obs.features
            reasons: list[str] = []
            if features.get("policy_violation") is True:
                reasons.append("policy_violation_feature")
            if features.get("authorization") == "denied":
                reasons.append("authorization_denied")
            if str(features.get("sensitivity", "")).lower() == "high" or "high" in [s.lower() for s in obs.sensitivity]:
                reasons.append("high_sensitivity_flow")
            anomaly = features.get("anomaly_score", 0.0)
            if isinstance(anomaly, (int, float)) and anomaly >= 0.85:
                reasons.append("high_anomaly_score")
            score = 0.1
            if reasons:
                score = 0.95 if any(r in reasons for r in ["policy_violation_feature", "authorization_denied"]) else 0.72
                if "high_anomaly_score" in reasons:
                    score = max(score, min(1.0, float(anomaly)))
            edges.append(InteractionEdge(
                edge_id=obs.event_id, source=source, target=target,
                event_type=obs.event_type, target_ref=obs.target_ref,
                risk_score=score, risk_reasons=reasons, evidence_refs=[obs.event_id],
            ))

        # Temporal path candidates: an edge can continue when its target is the next edge's source.
        candidates: list[CandidatePath] = []
        for index, edge in enumerate(edges):
            if not edge.risk_reasons:
                continue
            path_nodes = [edge.source, edge.target]
            refs = [edge.edge_id]
            score = edge.risk_score
            current = edge.target
            for next_edge in edges[index + 1:]:
                if current == "unknown":
                    break
                if next_edge.source != current or next_edge.target in path_nodes:
                    continue
                path_nodes.append(next_edge.target)
                refs.append(next_edge.edge_id)
                score = max(score, next_edge.risk_score)
                current = next_edge.target
                if len(refs) >= 3:
                    break
            candidates.append(CandidatePath(
                nodes=path_nodes, edge_refs=refs, score=score,
                rationale=("Path candidate is assembled from temporally ordered observed edges. "
                           "It is a lead for investigation, not proof of coordinated intent."),
            ))
        candidates.sort(key=lambda item: (item.score, len(item.edge_refs)), reverse=True)
        return InteractionGraphReport(
            nodes=list(nodes.values()), edges=edges, candidate_paths=candidates[:20],
            node_count=len(nodes), edge_count=len(edges),
            suspicious_edge_count=sum(bool(edge.risk_reasons) for edge in edges),
            limitations=[
                "Graph edges reflect only captured observations; missing events can hide or distort paths.",
                "Risk scores are heuristic indicators and are not calibrated probabilities.",
                "A candidate path does not establish collusion or causality.",
            ],
        )


class GraphAwareDetector:
    """Translate graph edges into findings; duplicate targets are merged by the composite detector."""
    def __init__(self, analyzer: InteractionGraphAnalyzer | None = None) -> None:
        self.analyzer = analyzer or InteractionGraphAnalyzer()

    def detect(self, observations: list[Observation]) -> list[DetectionFinding]:
        graph = self.analyzer.analyze(observations)
        by_id = {item.event_id: item for item in observations}
        findings = []
        for edge in graph.edges:
            if not edge.risk_reasons:
                continue
            obs = by_id.get(edge.edge_id)
            if obs is None or not obs.target_ref:
                continue
            severity = "high" if edge.risk_score >= 0.85 else "medium"
            risk_type = "unauthorized_information_flow" if any(
                reason in edge.risk_reasons for reason in ["policy_violation_feature", "authorization_denied"]
            ) else "suspicious_information_flow"
            findings.append(DetectionFinding(
                risk_type=risk_type, severity=severity,
                target_type=("tool_call" if obs.event_type == "tool_call" else
                             "tool_result" if obs.event_type == "tool_result" else "message"),
                target_ref=obs.target_ref, score=edge.risk_score,
                rationale="Graph layer found an observed edge with risk-associated metadata; review alternatives.",
                evidence_refs=[obs.event_id], detector_version="graph-0.3",
                score_semantics="uncalibrated_graph_edge_risk_score",
                alternative_explanations=["Metadata may be incomplete or misclassified.", "The path may be benign under a policy not represented in this trace."],
                recommended_next_actions=["inspect_path_provenance", "run_matched_control"],
            ))
        return findings


class BehavioralDetector:
    """Small behavioral baseline based on observed tool fan-out, not semantic intent."""
    def __init__(self, min_tool_fanout: int = 4) -> None:
        self.min_tool_fanout = max(2, min_tool_fanout)

    def detect(self, observations: list[Observation]) -> list[DetectionFinding]:
        targets_by_actor: dict[str, set[str]] = defaultdict(set)
        events_by_actor: dict[str, list[Observation]] = defaultdict(list)
        for obs in observations:
            if obs.event_type == "tool_call" and obs.target:
                targets_by_actor[obs.actor_id].add(obs.target)
                events_by_actor[obs.actor_id].append(obs)
        findings=[]
        for actor, targets in targets_by_actor.items():
            if len(targets) < self.min_tool_fanout:
                continue
            events=events_by_actor[actor]
            obs=events[-1]
            if not obs.target_ref:
                continue
            score=min(0.85, 0.55 + 0.05 * (len(targets) - self.min_tool_fanout + 1))
            findings.append(DetectionFinding(
                risk_type="behavioral_tool_fanout_anomaly", severity="medium",
                target_type="tool_call", target_ref=obs.target_ref, score=score,
                rationale=(f"Actor {actor!r} called {len(targets)} distinct tools in one captured trace; "
                           "this is a heuristic fan-out anomaly, not proof of abuse."),
                evidence_refs=[item.event_id for item in events], detector_version="behavior-0.3",
                score_semantics="heuristic_fanout_score",
                alternative_explanations=["The task may legitimately require multiple tools.", "Trace window or actor identity may aggregate unrelated calls."],
                recommended_next_actions=["compare_with_task_baseline", "inspect_tool_authorization"],
            ))
        return findings


class MultiLayerDetector:
    """Fuse rule, graph, and behavior candidates while preserving distinct risk types."""
    def __init__(self, semantic_scorer=None, *, semantic_threshold: float = 0.65) -> None:
        from agentguard.core.detector import RuleBasedDetector
        from agentguard.core.semantic import SemanticDetector
        self.rule_detector = RuleBasedDetector()
        self.graph_detector = GraphAwareDetector()
        self.behavior_detector = BehavioralDetector()
        self.semantic_detector = SemanticDetector(semantic_scorer, threshold=semantic_threshold)
        self.layer_names = ["rule", "graph", "behavior"] + (["semantic_callback"] if self.semantic_detector.enabled else [])

    def detect(self, observations: list[Observation]) -> list[DetectionFinding]:
        findings = self.rule_detector.detect(observations)
        findings.extend(self.graph_detector.detect(observations))
        findings.extend(self.behavior_detector.detect(observations))
        findings.extend(self.semantic_detector.detect(observations))
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        best = {}
        for finding in findings:
            key = (finding.target_ref, finding.risk_type)
            current = best.get(key)
            if current is None or (severity_rank[finding.severity], finding.score) > (severity_rank[current.severity], current.score):
                best[key] = finding
        return list(best.values())
