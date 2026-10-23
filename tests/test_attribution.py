import pytest

from agentguard.contracts.attribution import AttributionNodeAnalysisRequest, PathShiftAnalysisRequest
from agentguard.core.attribution import analyze_attribution_node, analyze_path_shift


def node_request():
    families = [f"family-{i}" for i in range(10) for _ in range(2)]
    return AttributionNodeAnalysisRequest(
        node_id="sender.layer.8.resid",
        node_type="sender_activation",
        baseline_secret_recovery=[0.9, 0.88] * 10,
        intervened_secret_recovery=[0.2, 0.25] * 10,
        baseline_collusion_success=[0.9, 0.9] * 10,
        intervened_collusion_success=[0.1, 0.2] * 10,
        baseline_benign_utility=[0.95, 0.94] * 10,
        intervened_benign_utility=[0.92, 0.91] * 10,
        case_families=families,
        evidence_mode="synthetic",
        bootstrap_draws=300,
        permutation_draws=1000,
    )


def test_node_mediation_reports_recovery_effect_and_benign_utility_separately():
    report = analyze_attribution_node(node_request())
    assert report.node_id == "sender.layer.8.resid"
    assert report.causal_claim_status == "candidate_only"
    assert report.secret_recovery_mediation["n_case_families"] == 10
    assert report.secret_recovery_mediation["mediation_contribution_baseline_minus_intervened"] > 0.5
    assert report.collusion_success_mediation["mediation_contribution_baseline_minus_intervened"] > 0.5
    assert report.benign_utility_change["utility_change_intervened_minus_baseline"] < 0
    assert any("Synthetic" in note for note in report.limitations)


def test_node_analysis_rejects_mismatched_arrays_and_non_probability_metrics():
    payload = node_request().model_dump()
    payload["case_families"] = ["only-one-family"] * len(payload["case_families"])
    with pytest.raises(ValueError, match="case families"):
        AttributionNodeAnalysisRequest.model_validate(payload)
    payload = node_request().model_dump()
    payload["intervened_secret_recovery"][0] = 1.5
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        AttributionNodeAnalysisRequest.model_validate(payload)


def test_path_shift_metrics_detect_distribution_change():
    request = PathShiftAnalysisRequest(
        protocol_fingerprint_before="protocol-v1-fixed-seed",
        protocol_fingerprint_after="protocol-v1-fixed-seed",
        model_state_before="base-model",
        model_state_after="optimized-model",
        contributions_before={"sender.layer.8": 0.8, "public.token": 0.2},
        contributions_after={"sender.layer.8": 0.1, "public.token": 0.2, "receiver.layer.12": 0.7},
        evidence_mode="synthetic",
        top_k=2,
    )
    report = analyze_path_shift(request)
    assert report.metrics["total_variation_distance"] > 0.5
    assert report.metrics["cosine_distance"] > 0
    assert report.metrics["top_k_jaccard_overlap"] < 1
    assert report.metrics["node_count_union"] == 3
    assert "Synthetic" in report.interpretation


def test_path_shift_rejects_empty_mass_or_negative_contributions():
    base = {
        "protocol_fingerprint_before": "protocol-v1-fixed-seed",
        "protocol_fingerprint_after": "protocol-v1-fixed-seed",
        "model_state_before": "before",
        "model_state_after": "after",
        "contributions_before": {"a": 0.0},
        "contributions_after": {"a": 1.0},
    }
    with pytest.raises(ValueError, match="positive total mass"):
        PathShiftAnalysisRequest.model_validate(base)
    base["contributions_before"] = {"a": -1.0}
    with pytest.raises(ValueError, match="non-negative"):
        PathShiftAnalysisRequest.model_validate(base)


def test_path_shift_requires_matching_attribution_protocol_fingerprints():
    with pytest.raises(ValueError, match="same attribution protocol fingerprint"):
        PathShiftAnalysisRequest.model_validate({
            "protocol_fingerprint_before": "protocol-before-v1",
            "protocol_fingerprint_after": "protocol-after-v2",
            "model_state_before": "theta0",
            "model_state_after": "theta1",
            "contributions_before": {"node-a": 1.0},
            "contributions_after": {"node-a": 1.0},
        })
