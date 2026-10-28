"""Reproducible paired and case-family-clustered statistical summaries.

These utilities report uncertainty and multiplicity-adjusted values; they do not
establish causal validity unless the underlying design and provenance support it.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from statistics import mean, median, stdev


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _validate_paired(control: Sequence[float], treatment: Sequence[float]) -> list[float]:
    if len(control) != len(treatment):
        raise ValueError("control and treatment arrays must have the same length")
    if len(control) < 2:
        raise ValueError("at least two matched pairs are required")
    values = [float(v) for v in list(control) + list(treatment)]
    if any(not math.isfinite(v) for v in values):
        raise ValueError("all metric values must be finite")
    return [float(t) - float(c) for c, t in zip(control, treatment)]


def _paired_permutation_pvalue(differences: list[float], *, seed: int, draws: int = 20000) -> float:
    n = len(differences)
    observed = abs(mean(differences))
    if n <= 16:
        total = 1 << n
        extreme = 0
        for mask in range(total):
            statistic = abs(sum((v if (mask >> i) & 1 else -v) for i, v in enumerate(differences)) / n)
            if statistic >= observed - 1e-15:
                extreme += 1
        return extreme / total
    rng = random.Random(seed)
    extreme = 0
    for _ in range(draws):
        statistic = abs(sum(v if rng.getrandbits(1) else -v for v in differences) / n)
        if statistic >= observed - 1e-15:
            extreme += 1
    return (extreme + 1) / (draws + 1)


def paired_effect_summary(
    control: Sequence[float], treatment: Sequence[float], *, seed: int = 7,
    bootstrap_draws: int = 5000, permutation_draws: int = 20000,
) -> dict:
    """Summarize treatment-control paired differences with percentile CI and sign-flip test."""
    diffs = _validate_paired(control, treatment)
    n = len(diffs)
    observed = mean(diffs)
    rng = random.Random(seed)
    boot_means = [mean(rng.choices(diffs, k=n)) for _ in range(max(100, bootstrap_draws))]
    return {
        "n_pairs": n,
        "control_mean": mean(float(v) for v in control),
        "treatment_mean": mean(float(v) for v in treatment),
        "mean_treatment_minus_control": observed,
        "median_paired_difference": median(diffs),
        "paired_sd": stdev(diffs),
        "standard_error": stdev(diffs) / math.sqrt(n),
        "bootstrap_95_ci": [_quantile(boot_means, 0.025), _quantile(boot_means, 0.975)],
        "paired_permutation_pvalue_two_sided": _paired_permutation_pvalue(
            diffs, seed=seed, draws=permutation_draws
        ),
        "analysis": "paired bootstrap percentile CI + paired sign-flip permutation test",
        "limitations": [
            "Exploratory unless this endpoint and analysis were preregistered.",
            "Bootstrap assumes observed pairs are representative; clustered families require a family-level design.",
            "A p-value does not establish practical significance or causal validity by itself.",
        ],
    }


def clustered_paired_effect_summary(
    control: Sequence[float], treatment: Sequence[float], case_families: Sequence[str], *,
    seed: int = 7, bootstrap_draws: int = 5000, permutation_draws: int = 20000,
) -> dict:
    """Estimate an equal-weight mean of case-family mean paired effects.

    Bootstrap resamples whole families (not episodes). The permutation test flips signs
    at the family level. This protects against treating correlated episodes from one
    family as independent observations, at the cost of a family-average estimand.
    """
    diffs = _validate_paired(control, treatment)
    groups = list(map(str, case_families))
    if len(groups) != len(diffs):
        raise ValueError("case_families length must match the paired metric arrays")
    by_family: dict[str, list[float]] = {}
    for group, diff in zip(groups, diffs):
        if not group.strip():
            raise ValueError("case family identifiers must be non-empty")
        by_family.setdefault(group, []).append(diff)
    family_means = [mean(values) for values in by_family.values()]
    k = len(family_means)
    if k < 2:
        raise ValueError("clustered paired analysis requires at least two distinct case families")
    observed = mean(family_means)
    rng = random.Random(seed)
    boot_means = [mean(rng.choices(family_means, k=k)) for _ in range(max(100, bootstrap_draws))]
    if k <= 16:
        total = 1 << k
        extreme = 0
        for mask in range(total):
            permuted = [v if (mask >> i) & 1 else -v for i, v in enumerate(family_means)]
            if abs(mean(permuted)) >= abs(observed) - 1e-15:
                extreme += 1
        p_value = extreme / total
        permutation_method = "exact family-level sign-flip enumeration"
    else:
        extreme = 0
        for _ in range(permutation_draws):
            permuted = [v if rng.getrandbits(1) else -v for v in family_means]
            if abs(mean(permuted)) >= abs(observed) - 1e-15:
                extreme += 1
        p_value = (extreme + 1) / (permutation_draws + 1)
        permutation_method = f"Monte Carlo family-level sign-flip ({permutation_draws} draws)"
    family_sd = stdev(family_means)
    return {
        "n_pairs": len(diffs),
        "n_case_families": k,
        "control_mean": mean(float(v) for v in control),
        "treatment_mean": mean(float(v) for v in treatment),
        "mean_treatment_minus_control": observed,
        "median_family_effect": median(family_means),
        "family_effects": {family: mean(values) for family, values in by_family.items()},
        "family_effect_sd": family_sd,
        "family_level_standard_error": family_sd / math.sqrt(k),
        "cluster_bootstrap_95_ci": [_quantile(boot_means, 0.025), _quantile(boot_means, 0.975)],
        "family_level_sign_flip_pvalue_two_sided": p_value,
        "permutation_method": permutation_method,
        "estimand": "equal-weight family-level mean of case-family mean treatment-minus-control differences",
        "limitations": [
            "Inference is at the case-family level; few families yield coarse p-values and wide uncertainty.",
            "Families are assumed independent and representative of the target population.",
            "This statistical result does not by itself establish that the intervention was causally identified.",
        ],
    }


def adjust_pvalues(p_values: dict[str, float], method: str = "holm") -> dict[str, dict[str, float | bool]]:
    """Adjust a named family of p-values using Holm or Benjamini-Hochberg."""
    if method not in {"none", "holm", "benjamini_hochberg"}:
        raise ValueError("method must be none, holm, or benjamini_hochberg")
    names = list(p_values)
    values = {name: float(value) for name, value in p_values.items()}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values.values()):
        raise ValueError("p-values must be finite and in [0, 1]")
    m = len(values)
    if method == "none" or m == 0:
        adjusted = dict(values)
    elif method == "holm":
        ordered = sorted(values, key=values.get)
        adjusted = {}
        running = 0.0
        for rank, name in enumerate(ordered):
            running = max(running, min(1.0, (m - rank) * values[name]))
            adjusted[name] = running
    else:
        ordered = sorted(values, key=values.get)
        adjusted = {}
        running = 1.0
        for reverse_index in range(m - 1, -1, -1):
            name = ordered[reverse_index]
            rank = reverse_index + 1
            running = min(running, values[name] * m / rank)
            adjusted[name] = min(1.0, running)
    return {
        name: {"p_value": values[name], "adjusted_p_value": adjusted[name], "reject_at_0_05": adjusted[name] <= 0.05}
        for name in names
    }


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(float(value) for value in values), key=lambda item: item[1])
    ranks = [0.0] * len(ordered)
    index = 0
    while index < len(ordered):
        stop = index + 1
        while stop < len(ordered) and ordered[stop][1] == ordered[index][1]:
            stop += 1
        rank = (index + 1 + stop) / 2
        for position in range(index, stop):
            ranks[ordered[position][0]] = rank
        index = stop
    return ranks


def _spearman_rho(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    x = _average_ranks(left)
    y = _average_ranks(right)
    x_mean = mean(x)
    y_mean = mean(y)
    covariance = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    x_ss = sum((a - x_mean) ** 2 for a in x)
    y_ss = sum((b - y_mean) ** 2 for b in y)
    if x_ss <= 0 or y_ss <= 0:
        return None
    return covariance / math.sqrt(x_ss * y_ss)


def spearman_family_bootstrap(
    probe_scores: Sequence[float],
    intervention_effects: Sequence[float],
    case_families: Sequence[str],
    *,
    seed: int = 7,
    bootstrap_draws: int = 5000,
) -> dict:
    """Estimate held-out Spearman association with whole-family bootstrap intervals."""
    if not (len(probe_scores) == len(intervention_effects) == len(case_families)):
        raise ValueError("Probe scores, intervention effects, and case families must align")
    if len(probe_scores) < 4 or any(not math.isfinite(float(v)) for v in [*probe_scores, *intervention_effects]):
        raise ValueError("at least four finite Probe/effect pairs are required")
    grouped: dict[str, tuple[list[float], list[float]]] = {}
    for score, effect, family in zip(probe_scores, intervention_effects, case_families):
        key = str(family).strip()
        if not key:
            raise ValueError("case family identifiers must be non-empty")
        scores, effects = grouped.setdefault(key, ([], []))
        scores.append(float(score))
        effects.append(float(effect))
    if len(grouped) < 2:
        raise ValueError("at least two independent case families are required")
    rho = _spearman_rho(probe_scores, intervention_effects)
    if rho is None:
        return {
            "status": "undefined",
            "spearman_rho": None,
            "family_bootstrap_95_ci": None,
            "n_pairs": len(probe_scores),
            "n_case_families": len(grouped),
            "valid_bootstrap_draws": 0,
            "unknown_reason": "PROBE_OR_EFFECT_HAS_NO_RANK_VARIATION",
        }
    families = list(grouped.values())
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(max(100, bootstrap_draws)):
        selected = rng.choices(families, k=len(families))
        scores = [value for family_scores, _ in selected for value in family_scores]
        effects = [value for _, family_effects in selected for value in family_effects]
        sample_rho = _spearman_rho(scores, effects)
        if sample_rho is not None:
            draws.append(sample_rho)
    return {
        "status": "estimated" if len(draws) >= 100 else "undefined",
        "spearman_rho": rho,
        "family_bootstrap_95_ci": [_quantile(draws, 0.025), _quantile(draws, 0.975)] if len(draws) >= 100 else None,
        "n_pairs": len(probe_scores),
        "n_case_families": len(grouped),
        "valid_bootstrap_draws": len(draws),
        "limitations": [
            "Probe scope, target, layer, token, model revision, label semantics, and test split must match the preregistered study.",
            "The association is family-clustered and does not establish that Probe scores cause intervention effects.",
            "Bootstrap intervals may be unstable with few independent case families.",
        ],
    }
