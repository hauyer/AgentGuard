"""Compatibility import for the legacy direct StegoPot adapter.

Production audit execution must use WARDEN; this adapter is retained only for
existing offline integration callers until the public WARDEN runtime is complete.
"""

from agentguard.compat.stegopot import IntegrationNotConfigured, StegoPotAdapter

__all__ = ["IntegrationNotConfigured", "StegoPotAdapter"]
