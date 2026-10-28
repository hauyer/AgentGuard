from concurrent.futures import ThreadPoolExecutor

import pytest

from agentguard.contracts.research import StudyRegistrationRequest
from agentguard.research_registry import ResearchRegistry, canonical_json, sha256_json


def registration_request():
    return StudyRegistrationRequest(
        title="Targeted defense study",
        research_question="Does a targeted message defense reduce observed risk while preserving utility?",
        primary_hypothesis="The treatment reduces risk exposure compared with matched control runs.",
        primary_outcome="risk_exposure",
        primary_direction="decrease",
        secondary_outcomes=["benign_utility"],
        analysis_method="clustered_paired",
        multiplicity_method="holm",
        planned_pairs=20,
        planned_case_families=10,
        seed=17,
        matching_variables=["case_family", "seed", "model_revision"],
        inclusion_criteria=["Authorized isolated test scenario", "Valid matched control and treatment outputs"],
        exclusion_criteria=["Runtime crash before metric collection"],
        stopping_rule="Stop after 20 planned pairs or report all failures if the budget is exhausted.",
        missing_data_rule="Do not impute failed pairs; report missingness and use completed pairs only.",
        code_revision="test-revision",
    )


def test_registry_registration_is_immutable_and_hash_verifiable(tmp_path):
    registry = ResearchRegistry(tmp_path / "studies.sqlite3")
    study = registry.register(registration_request())
    assert registry.verify_registration(study.study_id) is True
    loaded = registry.get(study.study_id)
    assert loaded is not None
    assert loaded.registration_sha256 == study.registration_sha256
    assert loaded.primary_outcome == "risk_exposure"
    assert len(registry.list()) == 1


def test_registry_idempotency_replays_after_restart_and_rejects_payload_conflict(tmp_path):
    path = tmp_path / "studies.sqlite3"
    registry = ResearchRegistry(path)
    request = registration_request()
    first = registry.register(request, idempotency_key="study-key-1", actor_role="operator")
    restarted = ResearchRegistry(path)
    replay = restarted.register(request, idempotency_key="study-key-1", actor_role="approval")
    assert replay.study_id == first.study_id
    assert restarted.study_count() == 1
    with restarted._connect() as connection:
        row = connection.execute(
            "SELECT actor_role,status,resource_id,payload_sha256 FROM research_idempotency"
        ).fetchone()
    assert row["actor_role"] == "operator"
    assert row["status"] == "completed"
    assert row["resource_id"] == first.study_id
    assert len(row["payload_sha256"]) == 64

    changed = request.model_copy(update={"seed": request.seed + 1})
    from agentguard.research_registry import IdempotencyConflictError

    with pytest.raises(IdempotencyConflictError):
        restarted.register(changed, idempotency_key="study-key-1")


def test_concurrent_idempotent_registration_creates_one_immutable_study(tmp_path):
    path = tmp_path / "studies.sqlite3"
    registries = [ResearchRegistry(path) for _ in range(4)]
    request = registration_request()
    with ThreadPoolExecutor(max_workers=4) as pool:
        studies = list(pool.map(
            lambda registry: registry.register(request, idempotency_key="concurrent-study"),
            registries,
        ))
    assert len({study.study_id for study in studies}) == 1
    assert ResearchRegistry(path).study_count() == 1


def test_corrupt_registration_fails_integrity_without_repairing_history(tmp_path):
    from agentguard.research_registry import ResearchRegistryIntegrityError

    registry = ResearchRegistry(tmp_path / "studies.sqlite3")
    study = registry.register(registration_request())
    with registry._connect() as connection, connection:
        connection.execute(
            "UPDATE registered_studies SET payload_json=? WHERE study_id=?",
            ("not-json", study.study_id),
        )
    assert registry.verify_registration(study.study_id) is False
    with pytest.raises(ResearchRegistryIntegrityError):
        registry.get(study.study_id)


def test_duplicate_outcomes_and_invalid_family_plan_are_rejected():
    values = registration_request().model_dump()
    values["secondary_outcomes"] = ["risk_exposure"]
    with pytest.raises(ValueError):
        StudyRegistrationRequest.model_validate(values)
    values = registration_request().model_dump()
    values["planned_case_families"] = 30
    with pytest.raises(ValueError):
        StudyRegistrationRequest.model_validate(values)


def test_old_registration_hash_survives_optional_contract_fields_added_later(tmp_path):
    registry = ResearchRegistry(tmp_path / "studies.sqlite3")
    study = registry.register(registration_request())
    payload = study.model_dump(mode="json")
    new_optional_fields = {
        "track", "protocol_ref", "dataset_ref", "split_sha256", "case_family_ref",
        "model_id", "model_revision", "tokenizer_revision", "decoding_config_ref",
        "probe_scope", "probe_target", "probe_layer", "probe_token", "probe_validation_ref",
        "label_semantics", "metric_definitions", "metric_units", "arms", "dataset_version",
        "warden_contract_id", "warden_runtime_id", "warden_mode", "warden_probe_id",
        "warden_sae_id", "warden_capture_targets", "approval_requirements", "supersedes_study_id",
    }
    for field in new_optional_fields:
        payload.pop(field, None)
    registration_payload = dict(payload)
    registration_payload.pop("registration_sha256")
    registration_payload.pop("study_id")
    registration_payload.pop("registered_at")
    registration_payload.pop("status")
    payload["registration_sha256"] = sha256_json(registration_payload)
    with registry._connect() as connection, connection:
        connection.execute(
            "UPDATE registered_studies SET registration_sha256=?, payload_json=? WHERE study_id=?",
            (payload["registration_sha256"], canonical_json(payload), study.study_id),
        )
    assert registry.verify_registration(study.study_id) is True
