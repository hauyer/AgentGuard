import pytest

from agentguard.contracts.research import StudyRegistrationRequest
from agentguard.research_registry import ResearchRegistry


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


def test_duplicate_outcomes_and_invalid_family_plan_are_rejected():
    values = registration_request().model_dump()
    values["secondary_outcomes"] = ["risk_exposure"]
    with pytest.raises(ValueError):
        StudyRegistrationRequest.model_validate(values)
    values = registration_request().model_dump()
    values["planned_case_families"] = 30
    with pytest.raises(ValueError):
        StudyRegistrationRequest.model_validate(values)
