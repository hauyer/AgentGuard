"""Core package with lazy public exports to avoid loading the full runtime graph."""

__all__ = ["AgentGuardEngine"]


def __getattr__(name: str):
    if name == "AgentGuardEngine":
        from agentguard.core.orchestrator import AgentGuardEngine

        return AgentGuardEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
