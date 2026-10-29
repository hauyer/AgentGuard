"""Deterministic views from verified event artifacts; Probe never creates message edges."""
from __future__ import annotations

from agentguard.core.statistics import adjust_pvalues, clustered_paired_effect_summary


def project_graph(study_id, run_id, track, events, *, through_seq=None):
    nodes, edges, probes, interventions = {}, {}, [], []
    for event in events:
        if through_seq is not None and event["seq"] > through_seq:
            continue
        kind, data = event["type"], event["data"]
        actor = event.get("actor_id")
        if kind == "agent_started" and actor:
            node = nodes.setdefault(actor, {"id": actor, "label": actor.title(), "role": actor,
                "status": "active", "source_event_ids": []})
            node["source_event_ids"].append(event["event_id"])
        if kind in {"message_sent", "message_delivered", "message_blocked", "message_replaced"}:
            source, target = event.get("source_agent_id"), event.get("target_agent_id")
            if not source or not target or not event.get("message_id"):
                raise ValueError("Message event is missing identity")
            key = source + "->" + target
            edge = edges.setdefault(key, {"id": key, "source": source, "target": target,
                "message_ids": [], "channel_type": "message", "delivered_count": 0,
                "blocked_count": 0, "source_event_ids": []})
            if event["message_id"] not in edge["message_ids"]:
                edge["message_ids"].append(event["message_id"])
            edge["source_event_ids"].append(event["event_id"])
            if kind == "message_delivered":
                edge["delivered_count"] += 1
            if kind == "message_blocked":
                edge["blocked_count"] += 1
            if kind in {"message_blocked", "message_replaced"}:
                interventions.append({"id": event["event_id"], "arm_id": event["arm_id"],
                    "kind": "message_block" if kind == "message_blocked" else "message_replace",
                    "target": {"type": "edge", "id": key}, "execution_state": "applied",
                    "source_event_ids": [event["event_id"]]})
        if kind == "probe_observed":
            associated = next((edge["id"] for edge in edges.values()
                               if event.get("message_id") in edge["message_ids"]), None)
            probes.append({"id": event["event_id"], "associated_edge_id": associated,
                "agent_id": actor, "target_layer": data.get("layer"),
                "token_policy": data.get("token_policy"),
                "label_semantics": data.get("label_semantics"), "value": data.get("value"),
                "threshold": data.get("threshold"), "evaluation_split": data.get("evaluation_split"),
                "evidence_refs": event["evidence_refs"], "source_event_ids": [event["event_id"]],
                "claim_level": "linear_decodability", "threshold_source": data.get("threshold_source"),
                "probability_calibrated": False, "case_family": data.get("case_family")})
        if kind in {"intervention_planned", "intervention_applied", "intervention_failed"}:
            method = data["kind"]
            target = data.get("target", actor)
            state = {"intervention_planned": "planned", "intervention_applied": "applied",
                     "intervention_failed": "failed"}[kind]
            interventions.append({"id": event["event_id"], "arm_id": event["arm_id"],
                "kind": method, "target": {"type": "agent" if method == "agent_isolation"
                                           else "edge" if method.startswith("message_") else "activation", "id": target},
                "execution_state": state, "source_event_ids": [event["event_id"]]})
            if method == "agent_isolation" and state == "applied" and target in nodes:
                nodes[target]["status"] = "isolated"
    return {"schema_version": "stego.graph/1", "study_id": study_id, "run_id": run_id,
        "track": track, "state": "observed" if events else "unavailable",
        "nodes": list(nodes.values()), "communication_edges": list(edges.values()),
        "probe_evidence": probes, "interventions": interventions,
        "evidence_status": "verified" if events else "not_run",
        "limitations": ["Probe secret_bit decodability is not proof of a covert causal channel.",
                         "No observed message event means no communication edge."]}


def compare_verified_runs(study, results):
    """Pair frozen held-out families and reuse the family-aware statistics implementation."""
    manifests = [item["manifest"] for result in results for item in result.get("artifacts", [])
                 if item["manifest"].get("schema_version") == "warden.research-run/2"]
    baseline = next((m for m in manifests if m["arm_id"] == "baseline"), None)
    if baseline is None:
        return {"schema_version": "stego.comparison/1", "status": "incomplete",
                "blockers": ["VERIFIED_S_BASELINE_MISSING"], "arms": []}
    rows = []
    for arm in study.arms:
        treatment = next((m for m in manifests if m["arm_id"] == arm.arm_id), None)
        if treatment is None:
            rows.append({"arm_id": arm.arm_id, "status": "not_run", "blockers": ["ARM_NOT_RUN"]})
            continue
        frozen = ("protocol_ref", "model_revision", "tokenizer_revision", "dataset_sha256",
                  "split_sha256", "seed", "config_sha256", "communication_mode",
                  "executor_sha256", "training_sha256")
        if any(treatment[key] != baseline[key] for key in frozen):
            raise ValueError("PAIRED_RUN_FROZEN_METADATA_MISMATCH")
        if treatment.get("model_files_sha256") != baseline.get("model_files_sha256"):
            raise ValueError("PAIRED_MODEL_WEIGHTS_MISMATCH")
        left = {o["case_family"]: o for o in baseline["outcomes"]}
        right = {o["case_family"]: o for o in treatment["outcomes"]}
        if set(left) != set(right):
            raise ValueError("PAIRED_CASE_FAMILIES_MISMATCH")
        family_ids = sorted(left)
        effects = {}
        for metric, field in (("SRR_ITT", "recovered"), ("NORMAL_UTILITY", "utility"), ("BER", "ber")):
            complete = [family for family in family_ids
                        if left[family][field] is not None and right[family][field] is not None]
            effects[metric] = (clustered_paired_effect_summary(
                [float(left[f][field]) for f in complete], [float(right[f][field]) for f in complete],
                complete, seed=study.seed, bootstrap_draws=1000, permutation_draws=2000)
                if len(complete) >= 2 else None)
        rows.append({"arm_id": arm.arm_id, "status": "paired_observed" if arm.arm_id != "baseline"
                     else "baseline", "baseline_run_id": baseline["run_id"],
                     "treatment_run_id": treatment["run_id"], "sample_count": len(family_ids),
                     "missing_count": sum(not o["executed"] for o in treatment["outcomes"]),
                     "case_families": family_ids,
                     "failed_count": sum(not o["executed"] for o in treatment["outcomes"]),
                     "undecodable_count": sum(o["executed"] and not o["decoded"] for o in treatment["outcomes"]),
                     "metrics": treatment["metrics"], "effects": effects,
                     "protocol_hash": treatment["config_sha256"], "model_revision": treatment["model_revision"],
                     "evidence_level": "verified_controlled_observation", "blockers": []})
    pvalues = {r["arm_id"]: r["effects"]["SRR_ITT"]["family_level_sign_flip_pvalue_two_sided"]
               for r in rows if r.get("status") == "paired_observed"
               and r["effects"]["SRR_ITT"] is not None}
    adjusted = adjust_pvalues(pvalues, method=study.multiplicity_method) if pvalues else []
    incomplete = any(row["status"] == "not_run" for row in rows)
    return {"schema_version": "stego.comparison/1", "status": "incomplete" if incomplete else "observed",
            "arms": rows, "adjusted_pvalues": adjusted, "multiplicity_method": study.multiplicity_method,
            "evidence_level": "verified_controlled_observation", "blockers": ["ARMS_NOT_RUN"] if incomplete else [],
            "limitations": ["Pilot estimates, not a claim of spontaneous collusion or causal validation.",
                             "Missing BER/utility values are excluded with denominators retained."]}
