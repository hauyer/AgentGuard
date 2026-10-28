import json
import sys
import types
from pathlib import Path

from agentguard import mcp_server


class _FakeAPI:
    def __init__(self):
        self.created = None

    def capabilities(self):
        return {"backend_mode": "synthetic"}

    def get_research_capabilities(self):
        return {"backend_status": "not_configured", "capabilities": []}

    def list_studies(self, **kwargs):
        return {"items": [], **kwargs}

    def get_study(self, study_id):
        return {"study_id": study_id}

    def kernel_status(self):
        return {"kernel_status": "unavailable"}

    def list_scenarios(self):
        return {"items": []}

    def list_investigations(self, **kwargs):
        return {"items": [], **kwargs}

    def get_investigation(self, request_id):
        return {"request_id": request_id}

    def get_evidence(self, request_id):
        return {"request_id": request_id, "items": []}

    def create_investigation(self, payload, *, request_id=None):
        self.created = (dict(payload), request_id)
        return {"status": "planned", "run": None}

    def preview_defense_policies(self, request_id):
        return {"request_id": request_id, "candidates": []}

    def search_verified_experiences(self, **kwargs):
        return {"items": [], **kwargs}


def test_mcp_tools_use_only_public_client_and_plan_without_starting_run(monkeypatch):
    class _FakeFastMCP:
        def __init__(self, name):
            self.name = name
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function
            return register

    mcp_module = types.ModuleType("mcp")
    mcp_module.__path__ = []
    server_module = types.ModuleType("mcp.server")
    server_module.__path__ = []
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    api = _FakeAPI()
    server = mcp_server.create_mcp_server(api)
    result = server.tools["create_investigation_plan"](
        request_id="mcp-request",
        contract_id="contract-1",
        model_id="model-1",
        runtime_id="runtime-1",
    )
    assert json.loads(result)["status"] == "planned"
    payload, request_id = api.created
    assert request_id == "mcp-request"
    assert payload["start_run"] is False
    assert payload["mode"] == "observation"
    assert json.loads(server.tools["get_research_capabilities"]())["backend_status"] == "not_configured"
    assert json.loads(server.tools["list_studies"](limit=5))["limit"] == 5
    assert json.loads(server.tools["get_study"]("study-1"))["study_id"] == "study-1"
    assert "execute_defense" not in server.tools
    assert "start_experiment" not in server.tools


def test_mcp_module_has_no_direct_runtime_or_kernel_imports():
    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert "agentguard.core" not in source
    assert "agentguard.compat" not in source
    assert "agentguard.adapters" not in source
    assert "agentguard.memory" not in source
    assert "WardenKernelClient" not in source
