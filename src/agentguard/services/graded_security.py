"""基于已核验图谱和显式政策配置的纯风险计算。"""
import json
from hashlib import sha256

from agentguard.contracts.graded_security import RiskAssessment, SecurityProfile, ValueEvent


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_targets(profile, event, graph, events):
    nodes = {n["id"] for n in graph["nodes"]}
    edges = {e["id"]: e for e in graph["communication_edges"]}
    if any(n.agent_id not in nodes for n in profile.nodes) or any(e.edge_id not in edges for e in profile.channels):
        raise ValueError("PROFILE_TARGET_NOT_OBSERVED")
    if event.study_id != graph["study_id"] or event.run_id != graph["run_id"] or profile.study_id != event.study_id:
        raise ValueError("EVENT_STUDY_RUN_MISMATCH")
    observed = next((e for e in events if e["event_id"] == event.event_id), None)
    if observed is None or not set(event.evidence_refs).issubset(set(observed["evidence_refs"])):
        raise ValueError("EVENT_EVIDENCE_NOT_BOUND")
    if event.target_type == "node":
        if len(event.target_ids) != 1 or event.target_ids[0] not in {observed.get("actor_id"), observed.get("source_agent_id"), observed.get("target_agent_id")} & nodes:
            raise ValueError("EVENT_NODE_MISMATCH")
    else:
        if event.target_type == "edge" and len(event.target_ids) != 1:
            raise ValueError("SINGLE_EDGE_REQUIRED")
        if len(set(event.target_ids)) != len(event.target_ids) or any(e not in edges for e in event.target_ids):
            raise ValueError("EVENT_EDGE_NOT_OBSERVED")
        if not any(event.event_id in edges[e]["source_event_ids"] or observed.get("message_id") in edges[e]["message_ids"] for e in event.target_ids):
            raise ValueError("EVENT_EDGE_EVIDENCE_MISMATCH")
        if event.target_type == "path" and any(edges[a]["target"] != edges[b]["source"] for a, b in zip(event.target_ids, event.target_ids[1:])):
            raise ValueError("PATH_NOT_CONTIGUOUS")
        if event.target_type == "path":
            family = observed.get("data", {}).get("case_family")
            if not family or any(not any(e["event_id"] in edges[k]["source_event_ids"]
                    and e.get("data", {}).get("case_family") == family for e in events) for k in event.target_ids):
                raise ValueError("PATH_FAMILY_EVIDENCE_NOT_BOUND")


def assess_risk(profile: SecurityProfile, event: ValueEvent, graph: dict) -> RiskAssessment:
    nodes = {n.agent_id: n.protection_level for n in profile.nodes}
    channels = {e.edge_id: e.assurance_level for e in profile.channels}
    edges = {e["id"]: e for e in graph["communication_edges"]}
    targets = ([{"source": event.target_ids[0], "target": event.target_ids[0], "id": None}]
               if event.target_type == "node" else [edges[k] for k in event.target_ids])
    bounds = []
    for edge in targets:
        levels = [nodes.get(edge[k]) for k in ["source", "target"]]
        q = channels.get(edge["id"])
        values = []
        for upper in [False, True]:
            s = max((v / 5 if v is not None else (1 if upper else .2)) for v in levels)
            exposure = (5 - q) / 4 if q is not None else float(upper)
            violation = int(event.policy_violation) if event.policy_violation is not None else int(upper)
            h = event.harm_level / 5 if event.harm_level is not None else float(upper)
            values.append(round(h * (profile.alpha * s + profile.beta * exposure + profile.gamma * violation), 12))
        bounds.append(values)
    lower, upper = (max(v[i] for v in bounds) for i in [0, 1])
    status = "NEEDS_REVIEW" if lower < profile.threshold <= upper else "BELOW_THRESHOLD" if upper < profile.threshold else "AT_OR_ABOVE_THRESHOLD"
    return RiskAssessment(lower=lower, upper=upper, risk_score=lower if lower == upper else None,
        status=status, policy_sha256=fingerprint(profile.model_dump()), explanation={
            "formula": "h(H)*(alpha*max(s(S_source),s(S_target))+beta*q(Q)+gamma*violation)",
            "aggregation": "max_per_edge", "node_only_channel_exposure": "unknown",
            "profile": profile.model_dump(), "event": event.model_dump(), "per_target_bounds": bounds})
