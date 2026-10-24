from agentguard.adapters.mock import MockBackend
from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status

__all__ = [
    "FractalBenchAdapter",
    "MockBackend",
    "StegoPotAdapter",
    "WardenKernelClient",
    "WardenKernelError",
    "warden_status",
]


def __getattr__(name: str):
    """Load deprecated integration adapters only when a caller requests them."""
    if name == "FractalBenchAdapter":
        from agentguard.compat.fractal_bench import FractalBenchAdapter

        return FractalBenchAdapter
    if name == "StegoPotAdapter":
        from agentguard.compat.stegopot import StegoPotAdapter

        return StegoPotAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
