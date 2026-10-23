import pytest

from agentguard.adapters.mock import MockBackend
from agentguard.api.app import create_app
from agentguard.core.orchestrator import AgentGuardEngine


def test_real_runtime_cannot_enter_agentguard_api_directly(tmp_path):
    class RealRuntime(MockBackend):
        def capabilities(self):
            return super().capabilities().model_copy(update={"backend_mode": "real"})

    backend = RealRuntime()
    engine = AgentGuardEngine(backend, backend, backend)
    with pytest.raises(ValueError, match="WARDEN kernel API"):
        create_app(engine, data_dir=tmp_path)


def test_direct_paired_executor_is_not_an_api_execution_backend(tmp_path):
    with pytest.raises(ValueError, match="through WARDEN"):
        create_app(data_dir=tmp_path, paired_executor=lambda **kwargs: None)
