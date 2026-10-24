"""Restricted MCP facade over the stable AgentGuard Public API.

Install with ``pip install -e '.[mcp]'``. Tools never instantiate local audit engines,
read the workflow database, approve actions, or call WARDEN directly.
"""
from __future__ import annotations

import json
from typing import Literal

from agentguard.sdk import AgentGuardAPIError, AgentGuardClient


def create_mcp_server(client: AgentGuardClient | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP support is optional. Install with: pip install -e '.[mcp]'") from exc

    api = client or AgentGuardClient.from_env()
    mcp = FastMCP("AgentGuard Research Tools")

    def render(call) -> str:
        try:
            return json.dumps(call(), ensure_ascii=False, indent=2)
        except AgentGuardAPIError as exc:
            return json.dumps({
                "error": exc.code,
                "status_code": exc.status_code,
                "request_id": exc.request_id,
                "message": str(exc),
            }, ensure_ascii=False)

    @mcp.tool()
    def get_capabilities() -> str:
        """Read AgentGuard's public capability manifest; no execution is started."""
        return render(api.capabilities)

    @mcp.tool()
    def get_kernel_status() -> str:
        """Read the WARDEN integration status reported by AgentGuard."""
        return render(api.kernel_status)

    @mcp.tool()
    def list_scenarios() -> str:
        """List scenarios exposed by the AgentGuard application API."""
        return render(api.list_scenarios)

    @mcp.tool()
    def list_investigations(status: str | None = None, limit: int = 50, offset: int = 0) -> str:
        """List durable investigation records using bounded API pagination."""
        return render(lambda: api.list_investigations(status=status, limit=limit, offset=offset))

    @mcp.tool()
    def get_investigation(request_id: str) -> str:
        """Read one durable investigation's status and safe summary."""
        return render(lambda: api.get_investigation(request_id))

    @mcp.tool()
    def get_investigation_evidence(request_id: str) -> str:
        """Read AgentGuard's governed evidence summary, never raw kernel artifact contents."""
        return render(lambda: api.get_evidence(request_id))

    @mcp.tool()
    def create_investigation_plan(
        request_id: str,
        contract_id: str,
        model_id: str,
        runtime_id: str,
        mode: Literal["observation", "capture_only", "detection"] = "observation",
        probe_id: str | None = None,
        sae_id: str | None = None,
        scenario_id: str | None = None,
        risk_type: str | None = None,
    ) -> str:
        """Ask AgentGuard to validate and create a bounded WARDEN plan; this tool never starts a run."""
        payload = {
            "request_id": request_id,
            "contract_id": contract_id,
            "model_id": model_id,
            "runtime_id": runtime_id,
            "mode": mode,
            "probe_id": probe_id,
            "sae_id": sae_id,
            "scenario_id": scenario_id,
            "risk_type": risk_type,
            "start_run": False,
        }
        return render(lambda: api.create_investigation(payload, request_id=request_id))

    @mcp.tool()
    def preview_defense_policies(request_id: str) -> str:
        """Create or read proposal-only defense candidates; approval and execution are unavailable in MCP."""
        return render(lambda: api.preview_defense_policies(request_id))

    @mcp.tool()
    def search_verified_experiences(
        query: str = "", scenario_id: str | None = None, risk_type: str | None = None,
        limit: int = 50,
    ) -> str:
        """Search only experiences that pass AgentGuard's verified-training evidence gate."""
        return render(lambda: api.search_verified_experiences(
            query=query, scenario_id=scenario_id, risk_type=risk_type, limit=limit,
        ))

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
