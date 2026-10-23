from agentguard.contracts.experience import ExperienceRecord
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
):
    values = {
        "experience_id": experience_id,
        "scenario_id": scenario_id,
        "case_family": "family-a",
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
    assert result["excluded_record_counts"]["synthetic_or_observational"] == 0
    assert result["excluded_record_counts"]["not_enabled"] == 1


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
    assert result["excluded_record_counts"]["inconclusive"] == 1
