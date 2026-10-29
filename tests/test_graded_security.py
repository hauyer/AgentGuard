"""政策风险区间、目标归属、不可变日志与动作拒绝的回归。"""
import math

import pytest
from pydantic import ValidationError

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.graded_security import SecurityProfile, ValueEvent
from agentguard.services.graded_registry import GradedRegistry
from agentguard.services.graded_security import assess_risk, validate_targets
from agentguard.services.intervention_decision import optimize_actions


def inputs(s=5, q=2, h=4, violation=True):
    p = SecurityProfile(profile_id="p", study_id="s", workflow_id="w", policy_version="v1", source_ref="approved-research",
        nodes=[{"agent_id": "sender", "protection_level": 4}, {"agent_id": "receiver", "protection_level": s}],
        channels=[{"edge_id": "sender->receiver", "assurance_level": q}])
    e = ValueEvent(event_id="e", study_id="s", run_id="r", target_type="edge", target_ids=["sender->receiver"],
        harm_level=h, business_value=5, policy_violation=violation, evidence_refs=["sha"], classification_source="approved-study")
    g = {"study_id": "s", "run_id": "r", "nodes": [{"id": "sender"}, {"id": "receiver"}],
         "communication_edges": [{"id": "sender->receiver", "source": "sender", "target": "receiver", "source_event_ids": ["e"], "message_ids": ["m"]}]}
    return p, e, g


def test_document_example_and_unknown_intervals():
    p, e, g = inputs()
    assert assess_risk(p, e, g).risk_score == .74
    for p, e, g in [inputs(s=None), inputs(q=None), inputs(h=None), inputs(violation=None)]:
        r = assess_risk(p, e, g)
        assert 0 <= r.lower <= r.upper <= 1
        assert r.risk_score is None
    p, e, g = inputs(q=None)
    p = p.model_copy(update={"threshold": .7})
    assert assess_risk(p, e, g).status == "NEEDS_REVIEW"


def test_monotonic_policy_levels():
    for variable, values in [("s", range(1, 6)), ("q", range(1, 6)), ("h", range(6))]:
        scores = [assess_risk(*inputs(**{variable: v})).risk_score for v in values]
        assert scores == sorted(scores, reverse=variable == "q")
    assert assess_risk(*inputs(violation=True)).lower >= assess_risk(*inputs(violation=False)).lower


@pytest.mark.parametrize("field,value", [("alpha", math.nan), ("beta", math.inf), ("threshold", -1), ("alpha", .5000000005)])
def test_nonfinite_and_invalid_policy_rejected(field, value):
    p, _, _ = inputs()
    with pytest.raises(ValidationError):
        SecurityProfile.model_validate({**p.model_dump(), field: value})


def test_evidence_and_run_are_bound_to_actual_event():
    p, e, g = inputs()
    observed = [{"event_id": "e", "evidence_refs": ["sha"], "message_id": "m"}]
    validate_targets(p, e, g, observed)
    for bad in [e.model_copy(update={"run_id": "another"}), e.model_copy(update={"evidence_refs": ["unrelated"]})]:
        with pytest.raises(ValueError):
            validate_targets(p, bad, g, observed)


def test_missing_cost_and_negative_utility_are_not_zero_filled():
    for estimate in [{}, {"residual_risk": .4, "utility_loss": -1, "normalized_cost": 0, "disruption": 0}]:
        with pytest.raises(ValueError):
            optimize_actions({"arm": estimate}, 5)
    estimate = {"residual_risk": .4, "utility_loss": .1, "normalized_cost": .2, "disruption": .3}
    assert optimize_actions({"b": estimate, "a": estimate}, 5)[0][1] == "a"


def test_decisions_survive_registry_recreation_and_scope_tampering(tmp_path):
    store = LocalArtifactStore(tmp_path)
    first = GradedRegistry(store).save("decision", "s", {"event_id": "e", "risk": .74})
    restored = GradedRegistry(LocalArtifactStore(tmp_path))
    assert restored.read("decision", "s", first["artifact_id"])["risk"] == .74
    with pytest.raises(ValueError):
        restored.read("decision", "other-study", first["artifact_id"])


def test_global_inversion_does_not_turn_a_label_flip_into_risk_reduction():
    from types import SimpleNamespace

    from agentguard.contracts.graded_security import OptimizationPolicy
    from agentguard.services.intervention_decision import verified_estimates

    n = 40
    identity = {k: "frozen" for k in ["model_revision", "tokenizer_revision", "dataset_sha256", "split_sha256", "batch_id", "replicate_id"]}
    identity.update(seed=42, status="completed", cost={"total_forward_passes": 50})
    def manifest(arm, inverse=False):
        return {**identity, "arm_id": arm, "metrics": {"UTILITY_COLLABORATIVE": {"value": 1., "denominator": n}},
                "outcomes": [{"case_family": str(i), "decoded": True, "recovered": (i % 10 != 0) != inverse} for i in range(n)]}
    rows = {"baseline": manifest("baseline"), "treatment": manifest("treatment", True)}
    service = SimpleNamespace(result=lambda _s, w: {"source_validation": "hash_and_study_verified", "artifacts": [{"manifest": rows[w]}]})
    comparison = {"selected_runs": [{"workflow_id": w} for w in rows]}
    policy = OptimizationPolicy(minimum_families=30, forward_pass_reference=100.,
                                permitted_arms=["treatment"], disruption_by_arm={"treatment": .1})
    estimates, diagnostics = verified_estimates(service, "s", comparison, policy, assess_risk(*inputs()))
    assert estimates == {}
    assert diagnostics["family_summaries"]["treatment"]["mean_treatment_minus_control"] == 0
