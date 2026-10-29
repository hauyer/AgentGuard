"""Layered system readiness and investigation option discovery.

Aggregation only: this service reuses the live WARDEN diagnostics probe and
never re-implements kernel capabilities. Readiness layers stay independent —
a healthy WARDEN is never promoted into model-runtime, audit-execution or
intervention-execution availability, and intervention execution stays
unsupported until the kernel explicitly declares an executor.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agentguard import __version__
from agentguard.adapters.warden import WardenKernelClient, WardenKernelError
from agentguard.contracts.system_readiness import (
    CaptureTargetSupport,
    InvestigationModeOption,
    InvestigationOptionsResponse,
    ReadinessCacheInfo,
    ReadinessCheck,
    ReadinessStatus,
    ResourceOption,
    SystemReadinessResponse,
)
from agentguard.services.warden_diagnostics import diagnose_kernel

_MODES = ("observation", "capture_only", "detection", "causal_audit", "defense_evaluation")
_CAPTURE_FIELDS = (
    "target_id", "agent_ids", "module", "layer", "component", "token_policy",
    "retain_gradient",
)
_NOT_PUBLISHED_NOTE = (
    "WARDEN's public API does not publish runtime identifiers; runtime_id stays "
    "caller-declared and unverified until a run is accepted."
)
_INTERVENTION_NOTE = (
    "No public intervention executor is declared by the kernel; approval can "
    "never turn this into execution support (DEP-007)."
)
_EVIDENCE_NOTE = (
    "AgentGuard's local content-addressed artifacts are readable; WARDEN "
    "external artifact download remains without a public API (DEP-004)."
)
_HEALTH_NOTE = "WARDEN health success never implies audit or intervention executability."
_LIVE_LOOP_GATE_BLOCKERS = (
    "AGENTGUARD_LOOP_OBSERVATION_DISABLED",
    "WARDEN_REMOTE_SUBMISSION_IDEMPOTENCY_UNSUPPORTED",
    "WARDEN_REMOTE_STATE_NOT_DURABLE",
    "WARDEN_REMOTE_AUTHORIZATION_UNVERIFIED",
    "WARDEN_EVIDENCE_PROVENANCE_UNVERIFIED",
)
_LIVE_LOOP_NOTE = (
    "Live investigation loops remain blocked: the loop has no observation adapter, "
    "and the reviewed WARDEN v1 API lacks remote submission idempotency, durable "
    "run state, verifiable remote authorization, and verifiable evidence provenance."
)
_STORAGE_TOPOLOGY_NOTE = (
    "AgentGuard state is supported on one host using SQLite and same-host file locks; "
    "these locks do not coordinate separate hosts. Multi-host active-active deployment is unsupported."
)


def client_from_env() -> tuple[WardenKernelClient | None, str | None]:
    """Return (client, None) or (None, error_code); misconfiguration is a state."""
    try:
        return WardenKernelClient.from_env(), None
    except WardenKernelError as exc:
        return None, exc.code


class SystemReadinessService:
    """Aggregate readiness with a short-lived in-process cache."""

    def __init__(
        self,
        client_provider: Callable[[], tuple[WardenKernelClient | None, str | None]] = client_from_env,
        *,
        ttl_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._client_provider = client_provider
        self._ttl = float(ttl_seconds)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._generated_wall: str = ""
        self._generated_mono: float = 0.0

    # ------------------------------------------------------------------ #
    # cached payload
    # ------------------------------------------------------------------ #
    def _snapshot(self, *, force_refresh: bool) -> tuple[dict[str, Any], ReadinessCacheInfo]:
        now = self._monotonic()
        with self._lock:
            fresh = (
                self._payload is not None
                and not force_refresh
                and now - self._generated_mono <= self._ttl
            )
            if fresh:
                assert self._payload is not None
                return self._payload, ReadinessCacheInfo(
                    source="cache", generated_at=self._generated_wall,
                    age_seconds=round(now - self._generated_mono, 3),
                    ttl_seconds=self._ttl, is_stale=False,
                    note="Served from the in-process readiness cache within its TTL.",
                )
            client, config_error = self._client_provider()
            payload = {"diagnosis": diagnose_kernel(client), "config_error": config_error}
            self._payload = payload
            self._generated_mono = now
            self._generated_wall = datetime.now(UTC).isoformat()
            return payload, ReadinessCacheInfo(
                source="live", generated_at=self._generated_wall,
                age_seconds=0.0, ttl_seconds=self._ttl, is_stale=False,
                note="Recomputed from live WARDEN probes; audit submissions still run their own preflight.",
            )

    # ------------------------------------------------------------------ #
    # readiness
    # ------------------------------------------------------------------ #
    def readiness(self, *, force_refresh: bool = False) -> SystemReadinessResponse:
        payload, cache = self._snapshot(force_refresh=force_refresh)
        diagnosis = payload["diagnosis"]
        config_error = payload["config_error"]
        checks = [ReadinessCheck(
            name="agentguard_api", status="available",
            last_checked_at=cache.generated_at, source="agentguard:self",
            capability_version=__version__,
        )]
        checks.append(ReadinessCheck(
            name="multi_host_storage",
            status="unsupported",
            last_checked_at=cache.generated_at,
            source="agentguard:storage_configuration",
            blockers=["MULTI_HOST_STORAGE_UNSUPPORTED"],
            diagnostics={
                "supported_topology": "single_host",
                "database": "SQLite",
                "lock_scope": "same_host_processes",
                "cross_host_locking": False,
            },
            capability_version="AgentGuard local SQLite store",
        ))
        status = str(diagnosis.get("status"))
        kernel_status = str(diagnosis.get("kernel_status") or "")
        transport_down = status in {"not_configured", "unavailable"}
        health_down = not transport_down and kernel_status != "ok"
        warden_down_blockers: list[str] = []
        if transport_down:
            warden_down_blockers = [
                str(
                    diagnosis.get("error_code")
                    or config_error
                    or "WARDEN_URL_NOT_CONFIGURED"
                )
            ]
        elif health_down:
            warden_down_blockers = ["WARDEN_HEALTH_NOT_OK"]
        down = transport_down or health_down

        def warden_check(
            name: str, status: ReadinessStatus, *, blockers: list[str] | None = None,
            diagnostics: dict[str, Any] | None = None, version: str | None = None,
        ) -> ReadinessCheck:
            return ReadinessCheck(
                name=name, status=status, last_checked_at=cache.generated_at,
                source="warden:diagnostics", blockers=blockers or [],
                diagnostics=diagnostics or {}, capability_version=version,
            )

        if transport_down:
            checks.append(warden_check(
                "warden_connectivity", "unavailable", blockers=warden_down_blockers,
            ))
        elif health_down:
            checks.append(warden_check(
                "warden_connectivity", "blocked", blockers=warden_down_blockers,
            ))
        else:
            checks.append(warden_check("warden_connectivity", "available"))

        capability_names = (
            "api_reachable", "kernel_available", "threat_contracts_available",
            "runtime_available", "probe_resources_available", "sae_resources_available",
            "whitebox_workflow_api_available", "intervention_api_available",
            "artifact_access_available", "research_execution_available",
            "persistent_run_available", "artifact_reader_available",
        )
        capability_states = diagnosis.get("capability_statuses")
        capability_states = capability_states if isinstance(capability_states, dict) else {}
        blocker_codes = {
            "api_reachable": "WARDEN_API_UNREACHABLE",
            "kernel_available": "WARDEN_API_CONTRACT_INCOMPATIBLE",
            "threat_contracts_available": "NO_THREAT_CONTRACTS",
            "runtime_available": "COMPONENT_UNAVAILABLE:agent.runtime",
            "probe_resources_available": "NO_READY_PROBE_RESOURCES",
            "sae_resources_available": "NO_READY_SAE_RESOURCES",
            "whitebox_workflow_api_available": "WARDEN_WHITEBOX_WORKFLOW_API_UNSUPPORTED",
            "intervention_api_available": "NO_PUBLIC_INTERVENTION_API:DEP-007",
            "artifact_access_available": "WARDEN_ARTIFACT_DOWNLOAD_NOT_PUBLIC:DEP-004",
            "research_execution_available": "WARDEN_RESEARCH_STAGE_ALLOWLIST_NOT_ADVERTISED",
            "persistent_run_available": "WARDEN_DURABLE_DISPATCHER_NOT_ADVERTISED",
            "artifact_reader_available": "WARDEN_ARTIFACT_READER_NOT_CONFIGURED",
        }
        for name in capability_names:
            if name == "api_reachable":
                capability_state = "unavailable" if transport_down else "available"
            elif transport_down:
                capability_state = "unknown"
            elif health_down and name == "kernel_available":
                capability_state = "blocked"
            elif health_down:
                capability_state = "unknown"
            else:
                capability_state = capability_states.get(name, "unknown")
            if capability_state not in {"available", "unavailable", "unknown", "unsupported", "blocked"}:
                capability_state = "unknown"
            capability_blockers: list[str] = []
            if capability_state not in {"available", "unknown"}:
                capability_blockers.append(blocker_codes[name])
            observed_blockers = diagnosis.get("research_blockers")
            if isinstance(observed_blockers, list):
                if name == "research_execution_available":
                    capability_blockers.extend(
                        item for item in observed_blockers
                        if isinstance(item, str) and item.startswith("WARDEN_RESEARCH_")
                    )
                elif name in {"persistent_run_available", "artifact_reader_available"}:
                    capability_blockers.extend(
                        item for item in observed_blockers
                        if isinstance(item, str) and item.startswith((
                            "WARDEN_DURABLE_", "WARDEN_CROSS_RESTART_", "WARDEN_ARTIFACT_",
                        ))
                    )
            if name == "kernel_available" and capability_state in {"unknown", "blocked"}:
                if diagnosis.get("api_contract_compatible") is False:
                    capability_blockers.append("WARDEN_API_CONTRACT_INCOMPATIBLE")
                else:
                    openapi_blocker = {
                        "unsupported": "WARDEN_OPENAPI_NOT_PUBLISHED",
                        "unavailable": "WARDEN_OPENAPI_ACCESS_DENIED"
                        if diagnosis.get("openapi_error_code") == "WARDEN_POLICY_DENIED"
                        else "WARDEN_OPENAPI_UNAVAILABLE",
                    }.get(diagnosis.get("openapi_status"), "WARDEN_OPENAPI_NOT_VERIFIED")
                    capability_blockers.append(openapi_blocker)
            checks.append(warden_check(
                name, capability_state,
                blockers=list(dict.fromkeys(capability_blockers)),
                diagnostics={"observed_capability_status": capability_state},
                version=(
                    str(diagnosis.get("openapi_summary", {}).get("service_version"))
                    if name == "kernel_available"
                    and isinstance(diagnosis.get("openapi_summary"), dict)
                    else None
                ),
            ))
        if down:
            for name in (
                "warden_api_compatibility", "contract_source", "available_contracts",
                "model_runtime", "probe_resources", "sae_resources", "audit_execution",
            ):
                checks.append(warden_check(name, "unknown", blockers=warden_down_blockers))
        else:
            api_compatible = diagnosis.get("api_compatible")
            checks.append(warden_check(
                "warden_api_compatibility",
                "available" if api_compatible is True else
                "blocked" if api_compatible is False else "unknown",
                blockers=[] if api_compatible is True else ["WARDEN_API_VERSION_INCOMPATIBLE"],
                version=str(diagnosis.get("api_version") or "unknown"),
            ))
            contracts = diagnosis.get("available_contracts")
            contract_source = diagnosis.get("contract_source_available")
            checks.append(warden_check(
                "contract_source",
                "available" if contract_source is True else
                "unknown" if contract_source == "unknown" else "unavailable",
            ))
            if isinstance(contracts, list) and contracts:
                checks.append(warden_check(
                    "available_contracts", "available",
                    diagnostics={"contract_count": len(contracts)},
                ))
            elif isinstance(contracts, list):
                checks.append(warden_check(
                    "available_contracts", "blocked", blockers=["NO_THREAT_CONTRACTS"],
                ))
            else:
                # The probe returned early (for example an API-version mismatch);
                # contract availability was not measured.
                checks.append(warden_check("available_contracts", "unknown"))
            runtime = diagnosis.get("runtime_available")
            checks.append(warden_check(
                "model_runtime",
                "available" if runtime is True else
                "unavailable" if runtime is False else "unknown",
                blockers=[] if runtime is True else ["COMPONENT_UNAVAILABLE:agent.runtime"],
            ))
            raw_resources = diagnosis.get("resources")
            resources = raw_resources if isinstance(raw_resources, list) else []
            for name, kind in (("probe_resources", "probe"), ("sae_resources", "sae")):
                entries = [r for r in resources if isinstance(r, dict) and r.get("kind") == kind]
                ready = [r for r in entries if r.get("state") == "ready"]
                if ready:
                    status: ReadinessStatus = "available"
                    blockers: list[str] = []
                elif entries:
                    status = "blocked"
                    blockers = [f"{kind.upper()}_RESOURCES_NOT_READY"]
                else:
                    status = "unknown"
                    blockers = [f"NO_{kind.upper()}_RESOURCES_DECLARED"]
                checks.append(warden_check(
                    name, status, blockers=blockers,
                    diagnostics={"declared": len(entries), "ready": len(ready)},
                ))
            execution = diagnosis.get("execution_available")
            checks.append(warden_check(
                "audit_execution",
                "available" if execution is True else
                "unavailable" if execution is False else "unknown",
                blockers=[
                    blocker for blocker in (diagnosis.get("blockers") or [])
                    if str(blocker).startswith("COMPONENT_UNAVAILABLE")
                ],
                diagnostics={"reference_mode": "causal_audit"},
            ))

        live_loop_blockers = list(_LIVE_LOOP_GATE_BLOCKERS)
        if status in {"not_configured", "unavailable"}:
            live_loop_blockers.extend(
                str(item) for item in diagnosis.get("blockers", [])
                if str(item) in {
                    "WARDEN_URL_NOT_CONFIGURED", "WARDEN_CONFIGURATION_ERROR",
                    "WARDEN_UNREACHABLE", "WARDEN_TIMEOUT", "WARDEN_TRANSPORT_ERROR",
                }
            )
        elif diagnosis.get("kernel_status") != "ok":
            live_loop_blockers.append("WARDEN_HEALTH_NOT_OK")
        if diagnosis.get("api_compatible") is not True and status not in {"not_configured", "unavailable"}:
            live_loop_blockers.append("WARDEN_API_VERSION_INCOMPATIBLE")
        checks.append(ReadinessCheck(
            name="live_loop_execution", status="blocked",
            last_checked_at=cache.generated_at,
            source="agentguard:warden_live_gate",
            blockers=list(dict.fromkeys(live_loop_blockers)),
            diagnostics={
                "observation_adapter": "disabled",
                "remote_submission_idempotency": "unsupported",
                "remote_run_state_after_restart": "unsupported",
                "remote_authorization": "unverified",
                "evidence_provenance": "unverified",
            },
            capability_version="AgentGuard I5 gate / WARDEN v1",
        ))

        intervention = self._intervention_status(diagnosis)
        checks.append(warden_check(
            "intervention_execution", intervention["status"],
            blockers=intervention["blockers"], diagnostics=intervention["diagnostics"],
        ))
        checks.append(ReadinessCheck(
            name="evidence_access", status="blocked",
            last_checked_at=cache.generated_at, source="agentguard:evidence_gateway",
            blockers=["WARDEN_ARTIFACT_DOWNLOAD_NOT_PUBLIC:DEP-004"],
            diagnostics={
                "local_content_addressed_store": True,
                "warden_artifact_download": "unsupported",
            },
        ))
        return SystemReadinessResponse(
            checks=checks, generated_at=cache.generated_at, cache=cache,
            limitations=[
                _HEALTH_NOTE, _INTERVENTION_NOTE, _EVIDENCE_NOTE,
                _LIVE_LOOP_NOTE, _STORAGE_TOPOLOGY_NOTE,
            ],
        )

    @staticmethod
    def _intervention_status(diagnosis: dict[str, Any]) -> dict[str, Any]:
        """Only an explicit kernel declaration can make interventions available."""
        capabilities = diagnosis.get("capabilities") or []
        for component in capabilities:
            if not isinstance(component, dict):
                continue
            component_id = str(component.get("component_id") or "")
            if "intervention" in component_id and component.get("available") is True:
                return {
                    "status": "available",
                    "blockers": [],
                    "diagnostics": {"declared_by": component_id},
                }
        return {
            "status": "unsupported",
            "blockers": ["NO_PUBLIC_INTERVENTION_EXECUTOR:DEP-007"],
            "diagnostics": {},
        }

    # ------------------------------------------------------------------ #
    # investigation options
    # ------------------------------------------------------------------ #
    def investigation_options(self, *, force_refresh: bool = False) -> InvestigationOptionsResponse:
        payload, cache = self._snapshot(force_refresh=force_refresh)
        diagnosis = payload["diagnosis"]
        config_error = payload["config_error"]
        contracts = diagnosis.get("available_contracts")
        contracts = contracts if isinstance(contracts, list) else []
        raw_resources = diagnosis.get("resources")
        resources = [item for item in raw_resources if isinstance(item, dict)] if isinstance(raw_resources, list) else []

        reachable = diagnosis.get("status") not in {"not_configured", "unavailable"}
        plan_blockers: list[str] = []
        if not reachable:
            plan_blockers = [config_error or "WARDEN_URL_NOT_CONFIGURED"]
        else:
            if diagnosis.get("kernel_status") != "ok":
                plan_blockers.append("WARDEN_HEALTH_NOT_OK")
            if diagnosis.get("api_compatible") is not True:
                plan_blockers.append("WARDEN_API_VERSION_INCOMPATIBLE")
            if diagnosis.get("api_contract_compatible") is not True:
                plan_blockers.append(
                    "WARDEN_API_CONTRACT_INCOMPATIBLE"
                    if diagnosis.get("api_contract_compatible") is False
                    else "WARDEN_OPENAPI_NOT_VERIFIED"
                )
            if not contracts:
                plan_blockers.append("NO_THREAT_CONTRACTS")
        plan_creation: ReadinessStatus = (
            "available" if reachable and not plan_blockers else "blocked"
        )

        availability = self._component_availability(diagnosis)
        activation = availability.get("activation.backend", "unknown")
        modes: list[InvestigationModeOption] = []
        for mode in _MODES:
            execution_flags = [availability.get("agent.runtime", "unknown"),
                               availability.get("run.dispatcher", "unknown")]
            if mode != "observation":
                execution_flags.append(activation)
            if False in execution_flags:
                run_execution: ReadinessStatus = "unavailable"
            elif "unknown" in execution_flags:
                run_execution = "unknown"
            else:
                run_execution = "available"
            blockers = list(plan_blockers)
            component_flags = (
                ("agent.runtime", availability.get("agent.runtime")),
                ("run.dispatcher", availability.get("run.dispatcher")),
                ("activation.backend", activation),
            )
            for component_id, flag in component_flags:
                if flag is False and (component_id != "activation.backend" or mode != "observation"):
                    blockers.append(f"COMPONENT_UNAVAILABLE:{component_id}")
            modes.append(InvestigationModeOption(
                mode=mode, plan_creation=plan_creation, run_execution=run_execution,
                submittable=plan_creation == "available" and run_execution == "available",
                blockers=list(dict.fromkeys(blockers)),
                note=(
                    "defense_evaluation additionally requires the AgentGuard approval token "
                    "before any submission."
                    if mode == "defense_evaluation" else ""
                ),
            ))

        missing = list(dict.fromkeys(
            blocker for option in modes for blocker in option.blockers
        ))
        for check in diagnosis.get("blockers") or []:
            missing.append(str(check))
        missing = list(dict.fromkeys(missing))[:64]
        runtime_ids = self._confirmed_runtime_ids(diagnosis)
        return InvestigationOptionsResponse(
            available=reachable and bool(contracts) and not plan_blockers,
            error_code=None if reachable else (config_error or "WARDEN_URL_NOT_CONFIGURED"),
            modes=modes,
            threat_contracts=[str(item) for item in contracts][:200],
            probe_resources=[self._resource_option(item, "probe") for item in resources if item.get("kind") == "probe"],
            sae_resources=[self._resource_option(item, "sae") for item in resources if item.get("kind") == "sae"],
            confirmed_runtime_ids=runtime_ids,
            capture_targets=CaptureTargetSupport(
                accepted_fields=list(_CAPTURE_FIELDS),
                activation_capture_available=activation is True,
                note=(
                    "Capture targets follow the WardenCaptureTarget contract; activation "
                    "capture requires the activation backend component. Runtime identifiers "
                    "are exactly those the kernel's capability metadata declared."
                ),
            ),
            missing_dependencies=missing,
            generated_at=cache.generated_at, cache=cache,
            limitations=[
                (
                    "Runtime identifiers come from the kernel's agent.runtime capability "
                    "metadata; caller-declared ids must match them or the plan is blocked."
                    if runtime_ids else _NOT_PUBLISHED_NOTE
                ),
                _INTERVENTION_NOTE,
                "Options reflect a point-in-time probe; each submission still runs its own preflight.",
            ],
        )

    @staticmethod
    def _resource_option(item: dict[str, Any], kind: str) -> ResourceOption:
        return ResourceOption(
            resource_id=str(item.get("resource_id") or ""),
            kind=kind,
            state=str(item.get("state") or "unknown"),
            version=str(item["version"]) if item.get("version") is not None else None,
        )

    @staticmethod
    def _component_availability(diagnosis: dict[str, Any]) -> dict[str, bool | str]:
        by_component = {
            str(item.get("component_id")): item
            for item in (diagnosis.get("capabilities") or []) if isinstance(item, dict)
        }

        def availability(component_id: str) -> bool | str:
            component = by_component.get(component_id)
            value = component.get("available") if component is not None else None
            return value if isinstance(value, bool) else "unknown"

        return {name: availability(name) for name in (
            "agent.runtime", "run.dispatcher", "activation.backend",
        )}

    @staticmethod
    def _confirmed_runtime_ids(diagnosis: dict[str, Any]) -> list[str]:
        """Runtime identifiers the kernel explicitly declared, never invented."""
        for component in diagnosis.get("capabilities") or []:
            if not isinstance(component, dict) or component.get("component_id") != "agent.runtime":
                continue
            if component.get("available") is not True:
                return []
            metadata = component.get("metadata")
            if not isinstance(metadata, dict):
                return []
            ids: list[str] = []
            runtime_id = metadata.get("runtime_id")
            if isinstance(runtime_id, str) and runtime_id.strip():
                ids.append(runtime_id.strip())
            for model_id in metadata.get("model_ids") or []:
                if isinstance(model_id, str) and model_id.strip():
                    ids.append(model_id.strip())
            return list(dict.fromkeys(ids))[:64]
        return []
