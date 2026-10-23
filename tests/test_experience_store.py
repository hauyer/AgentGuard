import pytest
from pydantic import ValidationError
from agentguard.contracts.experience import ExperienceRecord
from agentguard.memory.store import ExperienceStore


def _record(**overrides):
    data = dict(
        scenario_id="release_review", case_family="family-a", risk_type="unauthorized_information_flow",
        hypothesis_summary="Targeted message block reduces risk", action_type="message_block",
        action_summary="Block a single unauthorized message edge", outcome="success",
        evidence_grade="synthetic", verification_status="unverified", repetitions=1,
    )
    data.update(overrides)
    return ExperienceRecord(**data)


def test_experience_store_searches_and_never_recommends_unverified_records(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    record = store.add(_record())
    assert store.count() == 1
    assert store.search("message block", scenario_id="release_review")[0].experience_id == record.experience_id
    assert store.recommendations(scenario_id="release_review") == []
    assert store.action_statistics(verified_only=True) == []


def test_unverified_record_cannot_enable_auto_recommendation():
    with pytest.raises(ValidationError):
        _record(allow_auto_recommendation=True)


def test_verified_record_requires_repetitions_evidence_and_validation_notes():
    with pytest.raises(ValidationError):
        _record(verification_status="verified", allow_auto_recommendation=True,
                evidence_grade="causal_validated", repetitions=1,
                evidence_refs=["artifact://x"], validation_notes=["reviewed"])


def test_verified_success_can_be_recommended_only_with_explicit_opt_in(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    record = _record(
        verification_status="verified", allow_auto_recommendation=True,
        evidence_grade="interventional", repetitions=3,
        evidence_refs=["artifact://baseline", "artifact://treatment"],
        validation_notes=["Matched paired replay reviewed"],
    )
    store.add(record)
    recommendations = store.recommendations(scenario_id="release_review")
    assert len(recommendations) == 1
    assert recommendations[0].experience_id == record.experience_id
