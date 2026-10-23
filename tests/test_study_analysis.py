import pytest

from agentguard.contracts.research import StudyAnalysisRequest, StudyRegistrationRequest
from agentguard.core.study_analysis import analyze_registered_study
from agentguard.research_registry import ResearchRegistry


def request():
    return StudyRegistrationRequest(
        title="Risk and utility preregistration",
        research_question="Does a scoped intervention reduce risk exposure without material utility loss?",
        primary_hypothesis="Treatment reduces risk exposure relative to matched control at the case-family level.",
        primary_outcome="risk_exposure",
        primary_direction="decrease",
        secondary_outcomes=["benign_utility"],
        analysis_method="clustered_paired",
        multiplicity_method="holm",
        alpha=0.05,
        planned_pairs=20,
        planned_case_families=10,
        seed=11,
        matching_variables=["case_family", "seed", "model_config"],
        inclusion_criteria=["Authorized test runs with matching seed and model config"],
        exclusion_criteria=["Pre-metric runtime failure"],
        stopping_rule="Analyze after the planned 20 pairs; report all failed pairs.",
        missing_data_rule="No imputation; disclose incomplete pairs.",
    )


def data():
    families = [f"family-{i}" for i in range(10) for _ in range(2)]
    control_risk = [0.90, 0.88] * 10
    treatment_risk = [0.30, 0.32] * 10
    control_utility = [0.95, 0.94] * 10
    treatment_utility = [0.93, 0.92] * 10
    return StudyAnalysisRequest(
        outcomes={
            "risk_exposure": {"control": control_risk, "treatment": treatment_risk, "case_families": families},
            "benign_utility": {"control": control_utility, "treatment": treatment_utility, "case_families": families},
        },
        evidence_mode="synthetic",
        data_sha256=None,
        bootstrap_draws=300,
        permutation_draws=1000,
    )


def test_preregistered_analysis_uses_family_clusters_and_adjusts_secondary_endpoints(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite3")
    study = registry.register(request())
    report = analyze_registered_study(study, data())
    assert report.study_id == study.study_id
    assert report.registration_sha256 == study.registration_sha256
    assert report.evidence_mode == "synthetic"
    assert report.outcomes["risk_exposure"]["summary"]["n_case_families"] == 10
    assert report.outcomes["risk_exposure"]["summary"]["mean_treatment_minus_control"] < 0
    assert report.outcomes["risk_exposure"]["adjusted_p_value"] <= 0.05
    assert report.confirmatory_primary_result is True
    assert any("Synthetic evidence" in note for note in report.limitations)
    registry.add_analysis(report)
    assert len(registry.analyses(study.study_id)) == 1


def test_analysis_rejects_unregistered_outcome_and_missing_primary():
    # Use a temporary filesystem DB for stable behavior.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        registry = ResearchRegistry(Path(directory) / "registry.sqlite3")
        study = registry.register(request())
        good = data()
        unknown = good.model_copy(update={"outcomes": {**good.outcomes, "unregistered_metric": good.outcomes["risk_exposure"]}})
        with pytest.raises(ValueError, match="unregistered outcomes"):
            analyze_registered_study(study, unknown)
        missing_primary = good.model_copy(update={"outcomes": {"benign_utility": good.outcomes["benign_utility"]}})
        with pytest.raises(ValueError, match="primary outcome"):
            analyze_registered_study(study, missing_primary)


def test_analysis_rejects_missing_registered_secondary_and_mismatched_data_hash(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite3")
    study = registry.register(request())
    full = data()
    missing_secondary = full.model_copy(update={"outcomes": {"risk_exposure": full.outcomes["risk_exposure"]}})
    with pytest.raises(ValueError, match="all registered secondary outcomes"):
        analyze_registered_study(study, missing_secondary)
    wrong_hash = full.model_copy(update={"data_sha256": "c" * 64})
    with pytest.raises(ValueError, match="does not match"):
        analyze_registered_study(study, wrong_hash)
