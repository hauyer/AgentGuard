"""Compatibility imports for the optional caller-loaded PyTorch hook adapter.

Production white-box audits are owned by WARDEN. This module is kept for callers
that used the AgentGuard 0.9 Python API while WARDEN completes equivalent public
runtime support.
"""

from agentguard.compat.whitebox_activation import (
    ActivationInterventionResult,
    ActivationSnapshot,
    WhiteBoxActivationAdapter,
    WhiteBoxAdapterError,
)

__all__ = [
    "ActivationInterventionResult",
    "ActivationSnapshot",
    "WhiteBoxActivationAdapter",
    "WhiteBoxAdapterError",
]
