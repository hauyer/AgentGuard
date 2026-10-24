__all__ = ["AgentCoordinator"]


def __getattr__(name: str):
    if name == "AgentCoordinator":
        from agentguard.agents.coordinator import AgentCoordinator

        return AgentCoordinator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
