"""Reject known evaluator-only fields before they enter agent or workflow data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_DENIED_FIELDS = frozenset({
    "truth", "label", "labels", "oracle", "is_positive", "target_label",
    "is_true_stego", "oracle_label", "ground_truth_path", "trial_truth",
    "ground_truth", "ground_truth_label", "oracle_secret", "hidden_label",
    "truth_label", "answer_key",
})
_NORMALIZED_DENIED_FIELDS = frozenset(
    "".join(character for character in field_name if character.isalnum())
    for field_name in _DENIED_FIELDS
)


def find_hidden_truth_fields(value: Any) -> set[str]:
    """Return denied key names found recursively without returning their values."""
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                normalized = "".join(character for character in key.casefold() if character.isalnum())
                if normalized in _NORMALIZED_DENIED_FIELDS:
                    found.add(key)
            found.update(find_hidden_truth_fields(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found.update(find_hidden_truth_fields(nested))
    return found
