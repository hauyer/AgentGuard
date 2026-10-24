"""Optional local white-box instrumentation and intervention utilities."""
from agentguard.whitebox.activation import (
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
