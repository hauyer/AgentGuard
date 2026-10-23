import pytest

from agentguard.core.statistics import adjust_pvalues, clustered_paired_effect_summary


def test_clustered_paired_summary_resamples_families_and_is_reproducible():
    control = [0.9, 0.8, 0.9, 0.8, 0.9, 0.8, 0.9, 0.8]
    treatment = [0.2, 0.3, 0.2, 0.3, 0.2, 0.3, 0.2, 0.3]
    families = ["a", "a", "b", "b", "c", "c", "d", "d"]
    first = clustered_paired_effect_summary(control, treatment, families, seed=12, bootstrap_draws=500)
    second = clustered_paired_effect_summary(control, treatment, families, seed=12, bootstrap_draws=500)
    assert first == second
    assert first["n_pairs"] == 8
    assert first["n_case_families"] == 4
    assert first["mean_treatment_minus_control"] < 0
    assert first["cluster_bootstrap_95_ci"][1] < 0
    assert "family-level" in first["estimand"]


def test_clustered_summary_rejects_one_cluster_and_misaligned_data():
    with pytest.raises(ValueError):
        clustered_paired_effect_summary([1, 2], [0, 1], ["same", "same"])
    with pytest.raises(ValueError):
        clustered_paired_effect_summary([1, 2], [0, 1], ["a"])


def test_holm_and_bh_adjustment_are_bounded_and_monotone():
    values = {"primary": 0.01, "secondary_a": 0.03, "secondary_b": 0.20}
    holm = adjust_pvalues(values, "holm")
    bh = adjust_pvalues(values, "benjamini_hochberg")
    assert holm["primary"]["adjusted_p_value"] == pytest.approx(0.03)
    assert holm["secondary_a"]["adjusted_p_value"] == pytest.approx(0.06)
    assert bh["primary"]["adjusted_p_value"] <= bh["secondary_b"]["adjusted_p_value"]
    assert all(0 <= item["adjusted_p_value"] <= 1 for item in holm.values())


def test_adjust_pvalues_rejects_invalid_values():
    with pytest.raises(ValueError):
        adjust_pvalues({"x": float("nan")}, "holm")
    with pytest.raises(ValueError):
        adjust_pvalues({"x": 0.1}, "unknown")
