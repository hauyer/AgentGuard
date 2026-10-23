"""A dependency-free binary logistic linear probe for supplied numeric representations.

This module trains on caller-supplied vectors (for example, authorized hidden-state
summaries). It does not extract activations from a model or provide ground-truth labels.
"""
from __future__ import annotations
import math
import random
from collections import defaultdict
from typing import Sequence


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 700))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -700))
    return z / (1.0 + z)


class BinaryLinearProbe:
    def __init__(self, *, learning_rate: float = 0.1, epochs: int = 800, l2: float = 0.01, threshold: float = 0.5):
        if learning_rate <= 0 or epochs < 1 or l2 < 0 or not 0 < threshold < 1:
            raise ValueError("invalid logistic probe hyperparameters")
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.l2 = l2
        self.threshold = threshold
        self.means: list[float] = []
        self.scales: list[float] = []
        self.weights: list[float] = []
        self.bias = 0.0
        self.feature_names: list[str] = []
        self.training_samples = 0
        self.fitted = False

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int], feature_names: Sequence[str] | None = None) -> "BinaryLinearProbe":
        rows = [list(map(float, row)) for row in X]
        labels = list(y)
        if len(rows) != len(labels) or len(rows) < 4:
            raise ValueError("X and y must have the same length and at least four samples")
        if set(labels) != {0, 1}:
            raise ValueError("binary probe training requires both labels 0 and 1")
        dim = len(rows[0])
        if dim == 0 or any(len(row) != dim for row in rows):
            raise ValueError("all feature vectors must have the same non-zero dimension")
        if any(not math.isfinite(v) for row in rows for v in row):
            raise ValueError("features must be finite numbers")
        self.means = [sum(row[j] for row in rows) / len(rows) for j in range(dim)]
        self.scales = []
        for j in range(dim):
            variance = sum((row[j] - self.means[j]) ** 2 for row in rows) / max(1, len(rows) - 1)
            self.scales.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
        standardized = [[(row[j] - self.means[j]) / self.scales[j] for j in range(dim)] for row in rows]
        self.weights = [0.0] * dim
        self.bias = 0.0
        # Batch gradient descent; deterministic ordering and no hidden shuffling.
        for epoch in range(self.epochs):
            grad_w = [0.0] * dim
            grad_b = 0.0
            for row, label in zip(standardized, labels):
                probability = _sigmoid(sum(w * x for w, x in zip(self.weights, row)) + self.bias)
                error = probability - label
                grad_b += error
                for j, x in enumerate(row):
                    grad_w[j] += error * x
            scale = 1.0 / len(rows)
            step = self.learning_rate / math.sqrt(1.0 + epoch / 100.0)
            self.bias -= step * grad_b * scale
            self.weights = [w - step * (grad_w[j] * scale + self.l2 * w) for j, w in enumerate(self.weights)]
        self.feature_names = list(feature_names or [f"feature_{i}" for i in range(dim)])
        if len(self.feature_names) != dim:
            raise ValueError("feature_names length must match vector dimension")
        self.training_samples = len(rows)
        self.fitted = True
        return self

    def predict_scores(self, X: Sequence[Sequence[float]]) -> list[float]:
        if not self.fitted:
            raise RuntimeError("probe must be fit before prediction")
        results=[]
        for raw in X:
            row=list(map(float, raw))
            if len(row) != len(self.weights):
                raise ValueError("feature dimension mismatch")
            if any(not math.isfinite(v) for v in row):
                raise ValueError("features must be finite numbers")
            z=sum(w * ((row[j] - self.means[j]) / self.scales[j]) for j, w in enumerate(self.weights)) + self.bias
            results.append(_sigmoid(z))
        return results

    def predict(self, X: Sequence[Sequence[float]]) -> list[int]:
        return [int(score >= self.threshold) for score in self.predict_scores(X)]

    def to_dict(self) -> dict:
        if not self.fitted:
            raise RuntimeError("cannot export an unfitted probe")
        return {"model_type": "binary_logistic_linear_probe", "learning_rate": self.learning_rate,
                "epochs": self.epochs, "l2": self.l2, "threshold": self.threshold,
                "means": self.means, "scales": self.scales, "weights": self.weights,
                "bias": self.bias, "feature_names": self.feature_names,
                "training_samples": self.training_samples,
                "limitations": ["This artifact contains a trained linear classifier only; it does not identify a causal mechanism.",
                                "The model and label provenance must be recorded separately."]}

    @classmethod
    def from_dict(cls, payload: dict) -> "BinaryLinearProbe":
        if payload.get("model_type") != "binary_logistic_linear_probe":
            raise ValueError("unsupported probe artifact model_type")
        model=cls(learning_rate=float(payload["learning_rate"]), epochs=int(payload["epochs"]),
                  l2=float(payload["l2"]), threshold=float(payload["threshold"]))
        model.means=list(map(float,payload["means"])); model.scales=list(map(float,payload["scales"]))
        model.weights=list(map(float,payload["weights"])); model.bias=float(payload["bias"])
        model.feature_names=list(payload["feature_names"]); model.training_samples=int(payload["training_samples"])
        if not (len(model.means)==len(model.scales)==len(model.weights)==len(model.feature_names)):
            raise ValueError("invalid probe artifact dimensions")
        model.fitted=True
        return model


def split_by_group(y: Sequence[int], groups: Sequence[str], *, test_fraction: float = 0.2, seed: int = 7, attempts: int = 1000) -> tuple[list[int], list[int]]:
    """Return train/test indices with disjoint groups; retry to preserve both labels when possible."""
    labels=list(y); group_values=list(groups)
    if len(labels) != len(group_values) or len(labels) < 4:
        raise ValueError("y and groups must have equal length and at least four samples")
    if not 0.05 <= test_fraction <= 0.5:
        raise ValueError("test_fraction must be in [0.05, 0.5]")
    by_group: dict[str,list[int]]=defaultdict(list)
    for i, group in enumerate(group_values): by_group[str(group)].append(i)
    unique=list(by_group)
    if len(unique) < 2: raise ValueError("at least two distinct groups are required")
    rng=random.Random(seed)
    target=max(1,min(len(unique)-1,round(len(unique)*test_fraction)))
    for _ in range(attempts):
        rng.shuffle(unique)
        test_groups=set(unique[:target])
        test=[i for i,g in enumerate(group_values) if str(g) in test_groups]
        train=[i for i,g in enumerate(group_values) if str(g) not in test_groups]
        if len(set(labels[i] for i in train))==2 and len(set(labels[i] for i in test))==2:
            return train,test
    # Preserve group isolation even if class balance cannot be achieved.
    rng.shuffle(unique); test_groups=set(unique[:target])
    test=[i for i,g in enumerate(group_values) if str(g) in test_groups]
    train=[i for i,g in enumerate(group_values) if str(g) not in test_groups]
    return train,test


def split_by_group_three_way(
    y: Sequence[int],
    groups: Sequence[str],
    *,
    test_fraction: float = 0.2,
    validation_fraction: float = 0.15,
    seed: int = 7,
    attempts: int = 2000,
) -> tuple[list[int], list[int], list[int]]:
    """Split by case family into train/validation/test without group overlap.

    The function tries to retain both classes in each partition. If no group allocation
    can achieve that, it still returns a leak-free split; the caller must inspect classes.
    """
    labels = list(y)
    group_values = list(groups)
    if len(labels) != len(group_values) or len(labels) < 6:
        raise ValueError("y and groups must have equal length and at least six samples")
    if not 0.05 <= test_fraction <= 0.5 or not 0.05 <= validation_fraction <= 0.3:
        raise ValueError("test_fraction must be in [0.05, 0.5] and validation_fraction in [0.05, 0.3]")
    if test_fraction + validation_fraction >= 0.7:
        raise ValueError("test_fraction + validation_fraction must be below 0.7")
    by_group: dict[str, list[int]] = defaultdict(list)
    for index, group in enumerate(group_values):
        by_group[str(group)].append(index)
    unique_groups = list(by_group)
    if len(unique_groups) < 3:
        raise ValueError("at least three distinct case families are required")
    test_count = max(1, min(len(unique_groups) - 2, round(len(unique_groups) * test_fraction)))
    validation_count = max(1, min(len(unique_groups) - test_count - 1, round(len(unique_groups) * validation_fraction)))
    rng = random.Random(seed)
    for _ in range(attempts):
        rng.shuffle(unique_groups)
        test_groups = set(unique_groups[:test_count])
        validation_groups = set(unique_groups[test_count:test_count + validation_count])
        train_groups = set(unique_groups[test_count + validation_count:])
        train = [i for i, group in enumerate(group_values) if str(group) in train_groups]
        validation = [i for i, group in enumerate(group_values) if str(group) in validation_groups]
        test = [i for i, group in enumerate(group_values) if str(group) in test_groups]
        if all(set(labels[i] for i in part) == {0, 1} for part in (train, validation, test)):
            return train, validation, test
    rng.shuffle(unique_groups)
    test_groups = set(unique_groups[:test_count])
    validation_groups = set(unique_groups[test_count:test_count + validation_count])
    train_groups = set(unique_groups[test_count + validation_count:])
    train = [i for i, group in enumerate(group_values) if str(group) in train_groups]
    validation = [i for i, group in enumerate(group_values) if str(group) in validation_groups]
    test = [i for i, group in enumerate(group_values) if str(group) in test_groups]
    return train, validation, test


def select_f1_threshold(y_true: Sequence[int], scores: Sequence[float]) -> float:
    """Select an F1 threshold from validation data only; ties prefer fewer positives."""
    labels = list(y_true)
    values = list(map(float, scores))
    if len(labels) != len(values) or set(labels) != {0, 1}:
        raise ValueError("threshold selection requires paired scores and both binary classes")
    if any(not 0.0 <= score <= 1.0 for score in values):
        raise ValueError("scores must be in [0, 1]")
    candidates = sorted({0.000001, 0.5, 0.999999, *values})
    best_threshold = 0.5
    best_key = (-1.0, -0.5)
    for threshold in candidates:
        predictions = [int(score >= threshold) for score in values]
        tp = sum(y == 1 and pred == 1 for y, pred in zip(labels, predictions))
        fp = sum(y == 0 and pred == 1 for y, pred in zip(labels, predictions))
        fn = sum(y == 1 and pred == 0 for y, pred in zip(labels, predictions))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        key = (f1, threshold)
        if key > best_key:
            best_key = key
            best_threshold = threshold
    return min(0.999999, max(0.000001, best_threshold))
