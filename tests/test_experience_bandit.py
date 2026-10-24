import hashlib
from datetime import UTC, datetime

from agentguard.contracts.experience import ExperienceRecommendationPolicy, ExperienceRecord
from agentguard.core.experience_bandit import recommend_actions


def record(
    experience_id: str,
    action_type: str,
    outcome: str,
    *,
    scenario_id: str = "scenario-a",
    risk_type: str = "unauthorized_information_flow",
    verification_status: str = "verified",
    evidence_grade: str = "interventional",
    allow_auto_recommendation: bool = True,
    dataset_partition: str = "training",
    case_family: str = "family-a",
    execution_cost: float = 0.1,
    policy_version: str = "policy-v1",
):
    values = {
        "experience_id": experience_id,
        "scenario_id": scenario_id,
        "case_family": case_family,
        "dataset_partition": dataset_partition,
        "risk_type": risk_type,
        "hypothesis_summary": "test hypothesis",
        "action_type": action_type,
        "action_summary": "test action",
        "outcome": outcome,
        "evidence_grade": evidence_grade,
        "verification_status": verification_status,
        "repetitions": 2 if verification_status == "verified" else 1,
        "evidence_refs": ["artifact:abc"] if verification_status == "verified" else [],
        "validation_notes": ["reviewed paired outcomes"] if verification_status == "verified" else [],
        "allow_auto_recommendation": allow_auto_recommendation,
        "source_system": "warden" if verification_status == "verified" else "legacy",
        "source_run_status": "completed" if verification_status == "verified" else "unknown",
        "evidence_provenance_verified": verification_status == "verified",
        "audit_run_id": f"run-{experience_id}" if verification_status == "verified" else None,
        "metrics": {"risk_reduction": 0.4} if verification_status == "verified" else {},
        "observed_outcome": {"risk_reduction": 0.4} if verification_status == "verified" else {},
        "benign_utility": {"task_success_rate": 0.99} if verification_status == "verified" else {},
        "execution_cost": execution_cost if verification_status == "verified" else None,
        "model_version": "model-v1" if verification_status == "verified" else "unknown",
        "policy_version": policy_version if verification_status == "verified" else "unknown",
        "reproducibility_hash": hashlib.sha256(experience_id.encode()).hexdigest() if verification_status == "verified" else None,
        "verified_at": datetime(2026, 1, 1, tzinfo=UTC) if verification_status == "verified" else None,
    }
    return ExperienceRecord.model_validate(values)


def test_bandit_only_uses_verified_enabled_interventional_records():
    records = [
        record("a1", "block_tool_result", "success"),
        record("a2", "block_tool_result", "success"),
        record("b1", "replace_message", "failure"),
        record("b2", "replace_message", "failure"),
        record("unverified", "unsafe_action", "success", verification_status="unverified",
               evidence_grade="synthetic", allow_auto_recommendation=False),
        record("reviewed_synthetic", "synthetic_only", "success", verification_status="reviewed",
               evidence_grade="synthetic", allow_auto_recommendation=False),
        record("disabled", "disabled_action", "success", allow_auto_recommendation=False),
    ]
    result = recommend_actions(records, seed=123)
    assert result["recommendation_only"] is True
    assert result["execution_authorized"] is False
    assert result["eligible_record_count"] == 4
    assert len(result["items"]) == 2
    by_action = {item["action_type"]: item for item in result["items"]}
    assert by_action["block_tool_result"]["posterior_mean_reward"] > by_action["replace_message"]["posterior_mean_reward"]
    assert result["excluded_record_counts"]["not_verified"] == 2
    assert result["excluded_record_counts"]["evidence_not_interventional"] == 2
    assert result["excluded_record_counts"]["recommendation_not_enabled"] == 3


def test_bandit_is_reproducible_and_context_filtered():
    records = [
        record("a1", "action_a", "success"),
        record("b1", "action_b", "failure"),
        record("other", "action_other", "success", scenario_id="scenario-b"),
    ]
    first = recommend_actions(records, scenario_id="scenario-a", seed=8)
    second = recommend_actions(records, scenario_id="scenario-a", seed=8)
    assert first == second
    assert {item["action_type"] for item in first["items"]} == {"action_a", "action_b"}
    assert first["excluded_record_counts"]["context_mismatch"] == 1


def test_inconclusive_records_are_not_counted_as_rewards():
    records = [record("i1", "action_a", "inconclusive")]
    result = recommend_actions(records)
    assert result["items"] == []
    assert result["eligible_record_count"] == 0
    assert result["excluded_record_counts"]["outcome_inconclusive"] == 1


def test_bandit_never_uses_test_or_same_family_records_and_models_cost():
    training = record("train", "low_cost", "success", case_family="train-family", execution_cost=0.1)
    expensive = record("expensive", "high_cost", "success", case_family="other-family", execution_cost=10.0)
    test = record(
        "test", "test_only", "success", dataset_partition="test", case_family="test-family",
        verification_status="unverified", evidence_grade="observational", allow_auto_recommendation=False,
    )
    same_family = record("same-family", "leak", "success", case_family="target-family")
    policy = ExperienceRecommendationPolicy(
        policy_version="cost-test-v1", cost_penalty=0.5, cost_scale=1.0
    )
    report = recommend_actions(
        [training, expensive, test, same_family],
        scenario_id="scenario-a",
        case_family="target-family",
        target_partition="test",
        policy=policy,
        seed=10,
    )
    assert report["eligible_record_count"] == 2
    assert report["excluded_record_counts"]["not_training_partition"] == 1
    assert report["excluded_record_counts"]["same_case_family"] == 1
    assert "test_only" not in {item["action_type"] for item in report["items"]}
    by_action = {item["action_type"]: item for item in report["items"]}
    assert by_action["high_cost"]["mean_execution_cost"] > by_action["low_cost"]["mean_execution_cost"]
    for item in by_action.values():
        expected = item["thompson_sample"] - 0.5 * item["mean_execution_cost"] / (item["mean_execution_cost"] + 1.0)
        assert item["cost_adjusted_sample"] == round(expected, 6)
        assert item["reward_uncertainty"] > 0
    assert report["recommendation_policy_version"] == "cost-test-v1"
