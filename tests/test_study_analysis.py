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


def test_analysis_rejects_hidden_truth_evaluator_keys():
    values = data().model_dump(mode="python")
    values["outcomes"]["ground_truth"] = values["outcomes"]["risk_exposure"]
    with pytest.raises(ValueError, match="hidden evaluator-only"):
        StudyAnalysisRequest.model_validate(values)
    values = data().model_dump(mode="python")
    values["source_artifact_refs"] = ["file:///etc/passwd"]
    with pytest.raises(ValueError, match="local file paths"):
        StudyAnalysisRequest.model_validate(values)


def test_tracked_study_rejects_local_dataset_and_arm_paths():
    values = tracked_multiarms_request().model_dump(mode="python")
    values["dataset_ref"] = "C:\\private\\dataset.jsonl"
    with pytest.raises(ValueError, match="local path"):
        StudyRegistrationRequest.model_validate(values)
    values = tracked_multiarms_request().model_dump(mode="python")
    values["arms"][1]["target_ref"] = "/etc/passwd"
    with pytest.raises(ValueError, match="local path"):
        StudyRegistrationRequest.model_validate(values)


def tracked_multiarms_request():
    return StudyRegistrationRequest(
        title="Frozen multi-arm intervention study",
        research_question="How do message interventions change secret recovery and benign utility?",
        primary_hypothesis="Registered treatments reduce secret recovery under the frozen test protocol.",
        primary_outcome="secret_recovery",
        primary_direction="decrease",
        secondary_outcomes=["normal_utility"],
        analysis_method="clustered_paired",
        multiplicity_method="holm",
        planned_pairs=8,
        planned_case_families=4,
        seed=19,
        matching_variables=["case_family", "seed", "model_revision", "decoding_config"],
        inclusion_criteria=["Authorized held-out family with baseline and arm result"],
        stopping_rule="Stop at eight planned pairs or include all arm failures in the report.",
        missing_data_rule="Keep blocked and failed arms visible; do not impute their measurements.",
        track="live_steganography",
        protocol_ref="artifact:protocol-v1",
        dataset_ref="dataset:public-suite-v1",
        split_sha256="a" * 64,
        case_family_ref="dataset:public-suite-v1/families",
        model_id="model-7b",
        model_revision="model-7b@rev-1",
        tokenizer_revision="tokenizer@rev-1",
        decoding_config_ref="artifact:decode-config-v1",
        label_semantics="secret_bit",
        metric_definitions={"secret_recovery": "SRR_ITT", "normal_utility": "NORMAL_UTILITY"},
        probe_scope="receiver",
        probe_target="receiver.layer.8",
        probe_layer="8",
        probe_token="secret",
        probe_validation_ref="artifact:probe-validation-v1",
        arms=[
            {"arm_id": "baseline", "role": "baseline", "method_id": "no_op", "method_version": "1"},
            {"arm_id": "block", "role": "treatment", "method_id": "message_block", "method_version": "1", "control_arm_id": "baseline"},
            {"arm_id": "replace", "role": "treatment", "method_id": "message_replace", "method_version": "1", "control_arm_id": "baseline"},
            {"arm_id": "detector-ban", "role": "treatment", "method_id": "detector_ban", "method_version": "1", "control_arm_id": "baseline"},
        ],
    )


def tracked_multiarms_data():
    families = [f"family-{i}" for i in range(4) for _ in range(2)]

    def pairs(control, treatment):
        return {"control": control, "treatment": treatment, "case_families": families}

    outcomes = {
        "secret_recovery": pairs([0.8] * 8, [0.3] * 8),
        "normal_utility": pairs([0.9] * 8, [0.85] * 8),
    }
    arm_analyses = {
        "block": {"status": "completed", "outcomes": outcomes},
        "replace": {"status": "completed", "outcomes": {
            "secret_recovery": pairs([0.8] * 8, [0.2] * 8),
            "normal_utility": pairs([0.9] * 8, [0.4] * 8),
        }},
        "detector-ban": {"status": "completed", "outcomes": {
            "secret_recovery": pairs([0.8] * 8, [0.6] * 8),
            "normal_utility": pairs([0.9] * 8, [0.8] * 8),
        }},
    }
    return StudyAnalysisRequest(
        outcomes=outcomes,
        evidence_mode="synthetic",
        bootstrap_draws=200,
        permutation_draws=1000,
        arm_analyses=arm_analyses,
        probe_effect_association={
            "probe_scores": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
            "intervention_effects": [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85],
            "case_families": families,
            "probe_scope": "receiver",
            "target": "receiver.layer.8",
            "layer": "8",
            "token": "secret",
            "model_revision": "model-7b@rev-1",
            "label_semantics": "secret_bit",
            "split": "test",
        },
    )


def test_frozen_tracks_arms_and_probe_coordinates_are_analyzed_without_live_claims():
    study = tracked_multiarms_request()
    # The immutable contract hash is assigned by the normal registry path.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        registered = ResearchRegistry(Path(directory) / "registry.sqlite3").register(study)
    report = analyze_registered_study(registered, tracked_multiarms_data())
    assert report.arm_analyses["block"]["status"] == "completed"
    assert report.rq2_probe_effect_association["spearman_rho"] == pytest.approx(1.0)
    assert report.rq2_probe_effect_association["family_bootstrap_95_ci"] is not None
    assert len(report.rq3_safety_utility) == 3
    assert report.rq3_safety_utility[0]["safety_effect"] > 0
    dominated = next(row for row in report.rq3_safety_utility if row["arm_id"] == "detector-ban")
    assert dominated["pareto_frontier"] is False
    assert report.causal_claim_status == "synthetic_only"
    assert report.source_validation == "synthetic_only"


def test_missing_or_failed_arm_is_retained_with_null_metrics():
    import tempfile
    from pathlib import Path

    values = tracked_multiarms_data().model_dump(mode="python")
    values["arm_analyses"].pop("detector-ban")
    with tempfile.TemporaryDirectory() as directory:
        study = ResearchRegistry(Path(directory) / "registry.sqlite3").register(tracked_multiarms_request())
    report = analyze_registered_study(study, StudyAnalysisRequest.model_validate(values))
    missing = report.arm_analyses["detector-ban"]
    assert missing["status"] == "unknown"
    assert missing["outcomes"]["secret_recovery"]["effect"] is None
    assert missing["unknown_reason"] == "ARM_RESULT_NOT_PROVIDED"
    assert any("remains unknown" in item for item in report.deviations)
