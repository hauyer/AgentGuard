"""Compatibility imports for the retired in-tree Probe implementation.

New code must use WARDEN-managed Probe resources through the public kernel API.
"""

from agentguard.compat.linear_probe import (
    BinaryLinearProbe,
    select_f1_threshold,
    split_by_group,
    split_by_group_three_way,
)

__all__ = [
    "BinaryLinearProbe",
    "select_f1_threshold",
    "split_by_group",
    "split_by_group_three_way",
]
