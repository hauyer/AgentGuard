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
    summary: dict[str, Any] = {
        "kernel_status": "unknown", "api_compatible": "unknown",
        "runtime_available": "unknown", "activation_available": "unknown",
        "contract_source_available": "unknown", "available_contracts": "unknown",
        "ready_probes": "unknown", "ready_saes": "unknown",
        "execution_available": "unknown",
    }
    if client is None:
        return {
            **summary,
            "status": "not_configured",
            "kernel_status": "not_configured",
            "base_url": None,
            "blockers": ["WARDEN_URL_NOT_CONFIGURED"],
        }

    result: dict[str, Any] = {
        **summary, "base_url": client.base_url, "mode": mode, "blockers": [],
    }
    try:
        health = client.health()
        result["health"] = health
        result["kernel_version"] = health.get("version")
        result["api_version"] = health.get("api_version")
        result["kernel_status"] = health.get("status", "unknown")
        result["api_compatible"] = health.get("api_version") == "v1"
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
    result["available_contracts"] = contracts
    result["ready_probes"] = [
        item["resource_id"] for item in resources
        if item.get("kind") == "probe" and item.get("state") == "ready"
    ]
    result["ready_saes"] = [
        item["resource_id"] for item in resources
        if item.get("kind") == "sae" and item.get("state") == "ready"
    ]
    if not contracts:
        result["blockers"].append("NO_THREAT_CONTRACTS")
    by_component = {item.get("component_id"): item for item in capabilities}

    def availability(component_id: str) -> bool | Literal["unknown"]:
        component = by_component.get(component_id)
        value = component.get("available") if component is not None else None
        return value if type(value) is bool else "unknown"

    result["runtime_available"] = availability("agent.runtime")
    result["activation_available"] = availability("activation.backend")
    result["contract_source_available"] = (
        True if contracts else availability("fractal-bench")
    )
    required_components = ["agent.runtime", "run.dispatcher"]
    if mode != "observation":
        required_components.append("activation.backend")
    execution_flags = [availability(component) for component in required_components]
    result["execution_available"] = (
        False if False in execution_flags
        else "unknown" if "unknown" in execution_flags else True
    )
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
