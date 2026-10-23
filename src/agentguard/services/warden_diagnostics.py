"""Read-only deployment diagnostics for the authoritative WARDEN service."""

from __future__ import annotations

from typing import Any, Literal

from agentguard.adapters.warden import WardenKernelClient, WardenKernelError


def diagnose_kernel(
    client: WardenKernelClient | None,
    *,
    contract_id: str | None = None,
    probe_id: str | None = None,
    sae_id: str | None = None,
    mode: Literal[
        "observation", "capture_only", "detection", "causal_audit", "defense_evaluation"
    ] = "causal_audit",
) -> dict[str, Any]:
    """Inspect live API and execution prerequisites without creating a plan or run."""
    if client is None:
        return {
            "status": "not_configured",
            "base_url": None,
            "blockers": ["WARDEN_URL_NOT_CONFIGURED"],
        }

    result: dict[str, Any] = {"base_url": client.base_url, "mode": mode, "blockers": []}
    try:
        health = client.health()
        result["health"] = health
        result["kernel_version"] = health.get("version")
        result["api_version"] = health.get("api_version")
        if health.get("status") != "ok":
            result["blockers"].append("WARDEN_HEALTH_NOT_OK")
        if health.get("api_version") != "v1":
            result["blockers"].append("WARDEN_API_VERSION_INCOMPATIBLE")
        if result["blockers"]:
            result["status"] = "blocked"
            return result

        capabilities = client.capabilities()
        resources = client.resources()
        contracts = client.contracts()
    except WardenKernelError as exc:
        result.update(status="unavailable", error_code=exc.code, reason=str(exc))
        result["blockers"].append(exc.code)
        return result

    result["capabilities"] = capabilities
    result["resources"] = resources
    result["contracts"] = contracts
    if not contracts:
        result["blockers"].append("NO_THREAT_CONTRACTS")
    by_component = {item.get("component_id"): item for item in capabilities}
    required_components = ["agent.runtime", "run.dispatcher"]
    if mode != "observation":
        required_components.append("activation.backend")
    for component in required_components:
        report = by_component.get(component)
        if report is None or report.get("available") is not True:
            result["blockers"].append(f"COMPONENT_UNAVAILABLE:{component}")
    for component in ("stegopot.package", "fractal-bench.package"):
        report = by_component.get(component)
        if report is None or report.get("available") is not True:
            result["blockers"].append(f"COMPONENT_UNAVAILABLE:{component}")
    if contract_id is not None and contract_id not in contracts:
        result["blockers"].append(f"THREAT_CONTRACT_NOT_AVAILABLE:{contract_id}")
    for kind, resource_id in (("probe", probe_id), ("sae", sae_id)):
        if resource_id is None:
            continue
        resource = next(
            (item for item in resources if item.get("resource_id") == resource_id and item.get("kind") == kind),
            None,
        )
        if resource is None:
            result["blockers"].append(f"{kind.upper()}_RESOURCE_NOT_FOUND:{resource_id}")
        elif resource.get("state") != "ready":
            result["blockers"].append(f"{kind.upper()}_RESOURCE_NOT_READY:{resource_id}")
    result["status"] = "ready" if not result["blockers"] else "blocked"
    return result
