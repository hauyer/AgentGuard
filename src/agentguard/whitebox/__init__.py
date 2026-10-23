"""Optional local white-box instrumentation and intervention utilities."""
from agentguard.whitebox.activation import (
    ActivationSnapshot,
    ActivationInterventionResult,
    WhiteBoxActivationAdapter,
    WhiteBoxAdapterError,
)

__all__ = [
    "ActivationSnapshot",
    "ActivationInterventionResult",
    "WhiteBoxActivationAdapter",
    "WhiteBoxAdapterError",
]
