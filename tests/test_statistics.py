import pytest

from agentguard.core.statistics import paired_effect_summary


def test_repeated_clustered_reads_preserve_input_identity_and_cannot_mutate_prior_results():
    from agentguard.core.statistics import clustered_paired_effect_summary

    control, treatment, families = [1., 1., 0.], [0., 1., 0.], ["a", "b", "c"]
    original = clustered_paired_effect_summary(control, treatment, families, seed=91)
    expected = original["mean_treatment_minus_control"]
    original["family_effects"]["a"] = 999
    original["cluster_bootstrap_95_ci"][0] = 999
    again = clustered_paired_effect_summary(control, treatment, families, seed=91)
    assert again["family_effects"]["a"] == -1
    assert again["mean_treatment_minus_control"] == expected
    assert again["cluster_bootstrap_95_ci"][0] != 999
    changed = clustered_paired_effect_summary(control, [1., 1., 0.], families, seed=91)
    assert changed["mean_treatment_minus_control"] == 0


def test_paired_statistics_detects_known_direction_and_is_reproducible():
    control = [1.0, 0.9, 1.0, 0.8, 0.95]
    treatment = [0.2, 0.3, 0.25, 0.4, 0.2]
    a = paired_effect_summary(control, treatment, seed=5, bootstrap_draws=1000)
    b = paired_effect_summary(control, treatment, seed=5, bootstrap_draws=1000)
    assert a == b
    assert a["mean_treatment_minus_control"] < 0
    assert a["bootstrap_95_ci"][1] < 0
    assert 0 <= a["paired_permutation_pvalue_two_sided"] <= 1


def test_paired_statistics_rejects_mismatched_pairs_and_nonfinite_values():
    with pytest.raises(ValueError):
        paired_effect_summary([1, 2], [1])
    with pytest.raises(ValueError):
        paired_effect_summary([1, float("nan")], [0, 0])
