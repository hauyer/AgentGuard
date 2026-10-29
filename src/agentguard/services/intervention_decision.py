"""候选筛选与纯代价优化；模型执行继续使用 StudyExecutionService。"""
from agentguard.contracts.graded_security import (
    InterventionCandidate,
    InterventionDecision,
    SecurityProfile,
    ValueEvent,
)
from agentguard.services.graded_security import assess_risk, validate_targets

METHODS = {"component_ablation": "L1", "leace": "L2", "activation_patch": "L2",
           "behavior_steering": "L3", "agent_isolation": "L4", "message_block": "L4", "message_replace": "L4"}


def verified_estimates(service, study_id, comparison, policy, risk):
    """对冻结成员重算允许全局反转的家族配对诊断；返回完整估计和不确定性。"""
    from agentguard.core.statistics import adjust_pvalues, clustered_paired_effect_summary

    manifests = []
    for member in comparison["selected_runs"]:
        result = service.result(study_id, member["workflow_id"])
        if result.get("source_validation") != "hash_and_study_verified":
            raise ValueError("EFFECT_SOURCE_NOT_VERIFIED")
        manifests.extend(a["manifest"] for a in result.get("artifacts", []))
    baseline = next(m for m in manifests if m["arm_id"] == "baseline")
    base_rows = {o["case_family"]: o for o in baseline["outcomes"]}

    def binary(rows):
        raw = [int(o["recovered"]) for o in rows]
        inverse = [int(o["decoded"] and not o["recovered"]) for o in rows]
        return inverse if sum(inverse) > sum(raw) else raw

    estimates, diagnostics = {}, {}
    for m in manifests:
        arm = m["arm_id"]
        if arm == "baseline" or arm not in policy.permitted_arms:
            continue
        outcomes = m["outcomes"]
        families = [o["case_family"] for o in outcomes]
        if (len(set(families)) != len(families) or set(families) != set(base_rows)
                or len(families) < policy.minimum_families or m["status"] != "completed"
                or any(m[k] != baseline[k] for k in ["model_revision", "tokenizer_revision", "dataset_sha256", "split_sha256", "seed", "batch_id", "replicate_id"])):
            continue
        metric = m["metrics"].get("UTILITY_COLLABORATIVE", {})
        if metric.get("value") is None or metric.get("denominator") != len(families):
            continue
        if not 0 <= metric["value"] <= 1:
            raise ValueError("INVALID_COLLABORATIVE_UTILITY")
        forward = m.get("cost", {}).get("total_forward_passes")
        if forward is not None and (not isinstance(forward, (int, float)) or not 0 <= forward < float("inf")):
            raise ValueError("INVALID_MEASURED_FORWARD_COST")
        scope = policy.disruption_by_arm.get(arm)
        if forward is None or scope is None or forward > policy.forward_pass_reference or risk.risk_score is None:
            continue
        ordered_base = [base_rows[f] for f in families]
        control, treated = binary(ordered_base), binary(outcomes)
        summary = clustered_paired_effect_summary(control, treated, families, seed=baseline["seed"])
        diagnostics[arm] = summary
        utility = baseline["metrics"].get("UTILITY_COLLABORATIVE", {}).get("value")
        if utility is None:
            continue
        if not 0 <= utility <= 1:
            raise ValueError("INVALID_BASELINE_COLLABORATIVE_UTILITY")
        estimates[arm] = {"residual_risk": risk.risk_score * sum(treated) / len(treated),
            "utility_loss": max(0, utility - metric["value"]), "normalized_cost": forward / policy.forward_pass_reference,
            "disruption": scope}
    adjusted = adjust_pvalues({a: d["family_level_sign_flip_pvalue_two_sided"] for a, d in diagnostics.items()}, "holm")
    return {a: e for a, e in estimates.items() if adjusted[a]["reject_at_0_05"] and diagnostics[a]["cluster_bootstrap_95_ci"][1] < 0}, {"family_summaries": diagnostics, "holm": adjusted,
        "risk_mapping": "policy priority times max(raw recovery, fixed global inverse recovery); not leakage probability",
        "leakage_scope": "global inversion diagnostic; excludes adaptive decoders"}


def optimize_actions(estimates, business_value, weights=(.5, .3, .1, .1)):
    """对可信调用方已核验的完整估计计算J；缺失量不补零。"""
    import math

    if type(business_value) is not int or business_value not in range(1, 6) or len(weights) != 4 or any(not math.isfinite(v) or v < 0 for v in weights) or not any(weights):
        raise ValueError("INVALID_OPTIMIZATION_POLICY")
    scored = []
    for arm, estimate in estimates.items():
        values = [estimate.get(k) for k in ["residual_risk", "utility_loss", "normalized_cost", "disruption"]]
        if any(v is None or isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError("INCOMPLETE_OR_INVALID_EFFECT_ESTIMATE")
        scored.append((sum(w * v * (business_value / 5 if i == 1 else 1) for i, (w, v) in enumerate(zip(weights, values))), arm))
    return sorted(scored)


def decide(service, profile: SecurityProfile, event: ValueEvent, workflow_id, *, may_execute=False, batch_id=None):
    from agentguard.services.experiment_views import project_graph

    result, events = service.events(event.study_id, workflow_id)
    graph = project_graph(event.study_id, result["run"]["run_id"], "live_steganography", events)
    validate_targets(profile, event, graph, events)
    study = service.registry.get(event.study_id)
    risk = assess_risk(profile, event, graph)
    comparison = service.compare_batch(event.study_id, batch_id) if batch_id else None
    if comparison and not any(i["run_id"] == event.run_id for i in comparison["selected_runs"]):
        raise ValueError("EVENT_RUN_NOT_IN_SELECTED_BATCH")
    candidates = []
    policy = profile.optimization_policy
    estimates, estimation = ({}, {})
    if policy and comparison and comparison["status"] == "observed" and comparison.get("research_validity") == "baseline_gate_passed":
        estimates, estimation = verified_estimates(service, event.study_id, comparison, policy, risk)
    targets = set(event.target_ids)
    if event.target_type != "node":
        targets.update(n for e in graph["communication_edges"] if e["id"] in targets for n in [e["source"], e["target"]])
    for method, layer in METHODS.items():
        reasons, contract, arm_id, evidence = [], None, None, None
        if layer in {"L1", "L3"}:
            reasons.append("UNSUPPORTED")
        else:
            arm = next((a for a in study.arms if a.method_id == method or a.arm_id == method), None)
            if arm is None:
                reasons.append("FROZEN_ARM_NOT_REGISTERED")
            else:
                arm_id = arm.arm_id
                try:
                    checked = service.execution_contract(event.study_id, arm_id)
                    contract = checked["contract"]
                    if checked["status"] != "verified_frozen_contract":
                        reasons.append("CAPABILITY_UNAVAILABLE")
                    scope, _, reference = contract["target_ref"].partition(":")
                    target = reference.split(":", 1)[0] if scope == "activation" else reference
                    if target not in targets:
                        reasons.append("FROZEN_TARGET_MISMATCH")
                except (ValueError, RuntimeError):
                    reasons.append("FROZEN_CONTRACT_UNAVAILABLE")
        if not may_execute:
            reasons.append("EXECUTION_AUTHORIZATION_REQUIRED")
        if policy and arm_id and arm_id not in policy.permitted_arms:
            reasons.append("MANDATORY_POLICY_DENIED")
        if comparison:
            row = next((r for r in comparison["arms"] if r["arm_id"] == arm_id), None)
            if row:
                evidence = {"batch_id": batch_id, "replicate_id": comparison.get("replicate_id"),
                    "observed": row, "multiplicity": comparison.get("adjusted_pvalues", {}).get(arm_id),
                    "scope": "matched_pilot_not_generalized_effect", "source": "verified_batch_comparison"}
        if arm_id in estimates:
            evidence = {**(evidence or {}), "estimate": estimates[arm_id], "uncertainty": estimation}
        candidates.append(InterventionCandidate(level=layer, method_id=method, arm_id=arm_id,
            feasible=not reasons, reasons=reasons + ([] if arm_id in estimates else ["INSUFFICIENT_EFFECT_EVIDENCE"]) + ([] if policy else ["COST_AND_DISRUPTION_POLICY_NOT_REGISTERED"]),
            execution_contract=contract, effect_evidence=evidence))
    feasible = [c for c in candidates if c.feasible]
    ranking = []
    if policy and feasible and all(c.arm_id in estimates for c in feasible) and event.business_value is not None and risk.status != "NEEDS_REVIEW":
        ranking = optimize_actions({c.arm_id: estimates[c.arm_id] for c in feasible}, event.business_value,
            (policy.risk_weight, policy.utility_weight, policy.cost_weight, policy.disruption_weight))
        objectives = {arm: score for score, arm in ranking}
        candidates = [c.model_copy(update={"objective": objectives.get(c.arm_id)}) for c in candidates]
    return InterventionDecision(study_id=event.study_id, run_id=event.run_id, event_id=event.event_id,
        profile_artifact_id="", event_artifact_id="", risk=risk, candidates=candidates,
        recommended_arm_id=ranking[0][1] if ranking else None,
        decision_status="OPTIMIZED" if ranking else "NEEDS_REVIEW" if risk.status == "NEEDS_REVIEW" or not any(c.feasible for c in candidates) else "CANDIDATES_ONLY")
