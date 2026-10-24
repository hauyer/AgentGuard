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
            self._generated_wall = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))
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
            limitations=[_HEALTH_NOTE, _INTERVENTION_NOTE, _EVIDENCE_NOTE],
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
        return InvestigationOptionsResponse(
            available=reachable and bool(contracts) and not plan_blockers,
            error_code=None if reachable else (config_error or "WARDEN_URL_NOT_CONFIGURED"),
            modes=modes,
            threat_contracts=[str(item) for item in contracts][:200],
            probe_resources=[self._resource_option(item, "probe") for item in resources if item.get("kind") == "probe"],
            sae_resources=[self._resource_option(item, "sae") for item in resources if item.get("kind") == "sae"],
            confirmed_runtime_ids=[],
            capture_targets=CaptureTargetSupport(
                accepted_fields=list(_CAPTURE_FIELDS),
                activation_capture_available=activation is True,
                note=(
                    "Capture targets follow the WardenCaptureTarget contract; activation "
                    "capture requires the activation backend component."
                ),
            ),
            missing_dependencies=missing,
            generated_at=cache.generated_at, cache=cache,
            limitations=[
                _NOT_PUBLISHED_NOTE,
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
