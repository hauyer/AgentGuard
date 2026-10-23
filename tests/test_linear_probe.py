import json
import pytest
from agentguard.core.linear_probe import BinaryLinearProbe, split_by_group
from agentguard.core.classification_metrics import evaluate_binary_classifier


def test_binary_linear_probe_learns_separable_numeric_representations_and_roundtrips():
    X = [[-2.0, -1.0], [-1.5, -1.0], [-1.0, -0.5], [-0.5, -1.0],
         [0.5, 1.0], [1.0, 0.5], [1.5, 1.0], [2.0, 1.5]]
    y = [0, 0, 0, 0, 1, 1, 1, 1]
    model = BinaryLinearProbe(learning_rate=0.2, epochs=1200, l2=0.001).fit(X, y, ["h1", "h2"])
    scores = model.predict_scores(X)
    assert all(scores[i] < scores[i + 4] for i in range(4))
    restored = BinaryLinearProbe.from_dict(json.loads(json.dumps(model.to_dict())))
    assert restored.predict(X) == model.predict(X)
    metrics = evaluate_binary_classifier(y, scores)
    assert metrics["f1"] == 1.0
    assert metrics["auroc"] == 1.0


def test_linear_probe_rejects_bad_shapes_and_missing_class():
    with pytest.raises(ValueError):
        BinaryLinearProbe().fit([[1], [2], [3], [4]], [0, 0, 0, 0])
    with pytest.raises(ValueError):
        BinaryLinearProbe().fit([[1], [2], [3], [4]], [0, 1])


def test_group_split_never_leaks_case_families_between_train_and_test():
    groups = [f"family-{i}" for i in range(10) for _ in range(2)]
    labels = [i % 2 for i in range(10) for _ in range(2)]
    train, test = split_by_group(labels, groups, test_fraction=0.3, seed=11)
    assert set(groups[i] for i in train).isdisjoint(set(groups[i] for i in test))
    assert set(train).isdisjoint(set(test))


def test_binary_metrics_reject_single_class_test_set():
    with pytest.raises(ValueError):
        evaluate_binary_classifier([1, 1], [0.8, 0.9])


def test_three_way_group_split_keeps_each_family_in_exactly_one_partition():
    from agentguard.core.linear_probe import split_by_group_three_way
    groups = [f"family-{i}" for i in range(12) for _ in range(2)]
    labels = [i % 2 for i in range(12) for _ in range(2)]
    train, validation, test = split_by_group_three_way(
        labels, groups, test_fraction=0.25, validation_fraction=0.15, seed=9,
    )
    group_sets = [set(groups[i] for i in part) for part in (train, validation, test)]
    assert group_sets[0].isdisjoint(group_sets[1])
    assert group_sets[0].isdisjoint(group_sets[2])
    assert group_sets[1].isdisjoint(group_sets[2])
    assert all(set(labels[i] for i in part) == {0, 1} for part in (train, validation, test))
