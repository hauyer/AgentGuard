import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agentguard.contracts.experience import ExperienceRecommendationPolicy, ExperienceRecord
from agentguard.memory.store import ExperiencePartitionConflict, ExperienceStore


def _record(**overrides):
    data = {
        "scenario_id": "release_review", "case_family": "family-a", "dataset_partition": "training",
        "risk_type": "unauthorized_information_flow",
        "hypothesis_summary": "Targeted message block reduces risk", "action_type": "message_block",
        "action_summary": "Block a single unauthorized message edge", "outcome": "success",
        "evidence_grade": "synthetic", "verification_status": "unverified", "repetitions": 1,
    }
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
    with pytest.raises(ValidationError, match="safe opaque references"):
        _record(evidence_refs=["file:///private/model-output.json"])
    with pytest.raises(ValidationError, match="finite numbers"):
        _record(metrics={"risk_reduction": float("nan")})


def test_verified_record_requires_repetitions_evidence_and_validation_notes():
    with pytest.raises(ValidationError):
        _record(verification_status="verified", allow_auto_recommendation=True,
                evidence_grade="causal_validated", repetitions=1,
                evidence_refs=["artifact://x"], validation_notes=["reviewed"])
    with pytest.raises(ValidationError, match="benign_utility"):
        _verified(benign_utility={})


def test_verified_success_can_be_recommended_only_with_explicit_opt_in(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    record = _record(
        verification_status="verified", allow_auto_recommendation=True,
        source_system="warden", source_run_status="completed", evidence_provenance_verified=True,
        evidence_grade="interventional", repetitions=3,
        evidence_refs=["artifact://baseline", "artifact://treatment"],
        validation_notes=["Matched paired replay reviewed"],
        audit_run_id="run-verified-1",
        metrics={"risk_reduction": 0.4},
        observed_outcome={"risk_reduction": 0.4},
        benign_utility={"task_success_rate": 0.99},
        execution_cost=0.2,
        model_version="model-3b-v1",
        policy_version="policy-v2",
        reproducibility_hash=hashlib.sha256(b"verified-record").hexdigest(),
        verified_at=datetime.now(UTC),
    )
    store.add(record)
    recommendations = store.recommendations(scenario_id="release_review")
    assert len(recommendations) == 1
    assert recommendations[0].experience_id == record.experience_id


def _verified(**overrides):
    values = {
        "verification_status": "verified", "allow_auto_recommendation": True,
        "source_system": "warden", "source_run_status": "completed", "evidence_provenance_verified": True,
        "evidence_grade": "interventional", "repetitions": 2,
        "evidence_refs": ["artifact://run-verified"], "validation_notes": ["Independent paired review."],
        "audit_run_id": "run-verified", "metrics": {"risk_reduction": 0.3},
        "observed_outcome": {"risk_reduction": 0.3}, "benign_utility": {"task_success_rate": 0.98},
        "execution_cost": 0.2, "model_version": "model-v1", "policy_version": "policy-v1",
        "reproducibility_hash": hashlib.sha256(b"verified").hexdigest(), "verified_at": datetime.now(UTC),
    }
    values.update(overrides)
    return _record(**values)


def test_experience_search_filters_context_and_withholds_evaluation_splits(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.add(_record(experience_id="train", case_family="train-family", dataset_partition="training"))
    store.add(_record(experience_id="validation", case_family="validation-family", dataset_partition="validation"))
    store.add(_record(experience_id="test", case_family="test-family", dataset_partition="test"))
    store.add(_record(experience_id="heldout", case_family="heldout-family", dataset_partition="held_out_family"))

    assert {item.experience_id for item in store.search()} == {"train", "validation"}
    assert store.search(dataset_partition="test") == []
    assert store.search(dataset_partition="held_out_family") == []
    assert store.search(dataset_partition="test", include_evaluation_partitions=True)[0].experience_id == "test"


def test_experience_search_filters_action_versions_evidence_and_verification_time(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    verified = _verified(
        experience_id="filtered-record", case_family="filter-family", audit_run_id="filter-run",
        model_version="model-7b-r4", policy_version="policy-r3", evidence_grade="causal_validated",
        evidence_refs=["artifact://filter"],
    )
    store.add(verified)
    assert [item.experience_id for item in store.search(action_type="message_block")] == ["filtered-record"]
    assert store.search(model_version="model-3b") == []
    assert store.search(policy_version="policy-r3", evidence_grade="causal_validated") == [verified]
    assert store.search(verified_after=verified.verified_at) == [verified]


def test_case_family_cannot_cross_dataset_partitions(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.add(_record(experience_id="same-family-train", case_family="family-x", dataset_partition="training"))
    with pytest.raises(ExperiencePartitionConflict):
        store.add(_record(experience_id="same-family-test", case_family="family-x", dataset_partition="test"))


def test_reward_gate_excludes_expired_and_conflicting_records(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    old = _verified(
        experience_id="expired", case_family="family-expired", audit_run_id="run-expired",
        evidence_refs=["artifact://expired"], expires_at=datetime.now(UTC) - timedelta(days=1),
        verified_at=datetime.now(UTC) - timedelta(days=3),
    )
    store.add(old)
    assert store.recommendations() == []

    shared = {
        "scenario_id": "release_review", "case_family": "family-conflict", "dataset_partition": "training",
        "source_system": "warden", "source_run_status": "completed", "evidence_provenance_verified": True,
        "risk_type": "unauthorized_information_flow", "hypothesis_summary": "Same evidence, contradictory label",
        "action_type": "message_block", "action_summary": "Block one edge", "evidence_grade": "interventional",
        "verification_status": "verified", "repetitions": 2, "evidence_refs": ["artifact://same"],
        "validation_notes": ["Re-reviewed"], "metrics": {"risk_reduction": 0.2},
        "observed_outcome": {"risk_reduction": 0.2}, "benign_utility": {"task_success_rate": 0.99},
        "execution_cost": 0.2, "model_version": "model-v1", "policy_version": "policy-v1",
        "audit_run_id": "run-shared", "reproducibility_hash": hashlib.sha256(b"same").hexdigest(),
        "verified_at": datetime.now(UTC), "allow_auto_recommendation": True,
    }
    store.add(ExperienceRecord(experience_id="conflict-a", outcome="success", **shared))
    store.add(ExperienceRecord(experience_id="conflict-b", outcome="failure", **shared))
    assert len(store.conflict_groups()) == 1
    assert {item.experience_id for item in store.recommendations()} == set()


def test_legacy_sqlite_records_are_migrated_to_quarantine(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    record = _record(experience_id="legacy-record")
    payload = record.model_dump(mode="json")
    payload.pop("dataset_partition", None)
    payload["schema_version"] = "0.1"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE experiences (
            experience_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, case_family TEXT NOT NULL,
            risk_type TEXT NOT NULL, action_type TEXT NOT NULL, verification_status TEXT NOT NULL,
            outcome TEXT NOT NULL, allow_auto_recommendation INTEGER NOT NULL,
            created_at TEXT NOT NULL, payload_json TEXT NOT NULL)""")
        db.execute("INSERT INTO experiences VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            "legacy-record", record.scenario_id, record.case_family, record.risk_type,
            record.action_type, record.verification_status, record.outcome, 0,
            record.created_at.isoformat(), json.dumps(payload),
        ))

    store = ExperienceStore(path)
    legacy = store.get("legacy-record")
    assert legacy is not None
    assert legacy.schema_version == "0.1"
    assert legacy.dataset_partition == "held_out_family"
    assert store.recommendations() == []


def test_recommendation_policy_versions_can_be_rolled_back(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    alternative = ExperienceRecommendationPolicy(
        policy_version="experience-thompson-v2", cost_penalty=0.4, cost_scale=2.0
    )
    store.save_policy(alternative, activate=True)
    assert store.active_policy().policy_version == "experience-thompson-v2"
    store.rollback_policy("experience-thompson-v1", reason="validation_regression")
    assert store.active_policy().policy_version == "experience-thompson-v1"
    assert store.policy_history()[-1]["action"] == "rollback"
