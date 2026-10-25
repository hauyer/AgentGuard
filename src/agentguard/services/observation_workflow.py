"""Standardized real-observation workflow over the existing WARDEN lifecycle.

This service adds a versioned run-configuration layer on top of
`WardenLifecycleService`; it does not replace the durable journal, the kernel
client or the evidence gateway. Resolution validates the configuration against
the live kernel's own declarations (contracts, runtime identifiers, mode
support) instead of trusting caller-supplied demo identifiers. Observation-only
runs never expose a causal-intervention entry: the execution request is an
observation audit and the returned record carries observational semantics.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agentguard.adapters.warden import WardenKernelClient
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.run_config import (
    InvestigationRunConfig,
    ObservationRunRecord,
)
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.memory.store import ExperienceStore
from agentguard.services.system_readiness import client_from_env
from agentguard.services.warden_lifecycle import WardenLifecycleService
from agentguard.services.warden_workflow_store import WardenWorkflowStore

# Modes requiring the activation backend beyond the agent runtime.
_ACTIVATION_MODES = {"capture_only", "detection", "causal_audit", "defense_evaluation"}


class RunResolutionError(ValueError):
    """The run configuration cannot be resolved against the live kernel."""


class ObservationRunService:
    """Plan, execute and record standardized real-observation investigations."""

    def __init__(
        self,
        *,
        client: WardenKernelClient | None = None,
        workflows: WardenWorkflowStore,
        artifacts: LocalArtifactStore,
        experiences: ExperienceStore | None = None,
        client_provider=client_from_env,
    ) -> None:
        self._client = client
        self._client_provider = client_provider
        self._workflows = workflows
        self._artifacts = artifacts
        self._experiences = experiences

    def _kernel(self) -> WardenKernelClient:
        if self._client is not None:
            return self._client
        client, error = self._client_provider()
        if client is None:
            raise RunResolutionError(error or "WARDEN_URL_NOT_CONFIGURED")
        return client

    # ------------------------------------------------------------------ #
    # resolution
    # ------------------------------------------------------------------ #
    def resolve(self, config: InvestigationRunConfig) -> dict[str, Any]:
        """Validate a configuration against the live kernel's declarations.

        Returns a resolution report; never invents resources. `status` is
        ``ready`` or ``blocked`` with explicit blockers.
        """
        kernel = self._kernel()
        report: dict[str, Any] = {
            "run_config_id": config.run_config_id,
            "status": "blocked",
            "blockers": [],
            "declared_runtime_ids": [],
            "contracts_count": None,
        }
        try:
            health = kernel.health()
        except Exception as exc:  # noqa: BLE001 - any transport failure is a blocked resolution
            report["blockers"].append(f"WARDEN_UNREACHABLE:{type(exc).__name__}")
            return report
        if health.get("status") != "ok":
            report["blockers"].append("WARDEN_HEALTH_NOT_OK")
            return report
        if health.get("api_version") != "v1":
            report["blockers"].append("WARDEN_API_VERSION_INCOMPATIBLE")
            return report

        try:
            contracts = kernel.contracts()
            capabilities = kernel.capabilities()
        except Exception as exc:  # noqa: BLE001
            report["blockers"].append(f"WARDEN_PROBE_FAILED:{type(exc).__name__}")
            return report
        report["contracts_count"] = len(contracts)
        if config.contract_id not in contracts:
            report["blockers"].append(f"THREAT_CONTRACT_NOT_AVAILABLE:{config.contract_id}")

        by_component = {
            str(item.get("component_id")): item
            for item in capabilities if isinstance(item, dict)
        }
        runtime = by_component.get("agent.runtime") or {}
        declared: list[str] = []
        metadata = runtime.get("metadata") if isinstance(runtime.get("metadata"), Mapping) else {}
        if isinstance(metadata.get("runtime_id"), str):
            declared.append(metadata["runtime_id"])
        for model_id in metadata.get("model_ids") or []:
            if isinstance(model_id, str):
                declared.append(model_id)
        report["declared_runtime_ids"] = declared
        if runtime.get("available") is not True:
            report["blockers"].append("COMPONENT_UNAVAILABLE:agent.runtime")
        else:
            if config.runtime_id not in declared:
                report["blockers"].append(f"RUNTIME_ID_NOT_DECLARED:{config.runtime_id}")
            if config.model_id not in declared:
                report["blockers"].append(f"MODEL_ID_NOT_DECLARED:{config.model_id}")

        def available(component_id: str) -> bool | None:
            value = (by_component.get(component_id) or {}).get("available")
            return value if isinstance(value, bool) else None

        if available("run.dispatcher") is False:
            report["blockers"].append("COMPONENT_UNAVAILABLE:run.dispatcher")
        if config.audit_mode in _ACTIVATION_MODES and available("activation.backend") is False:
            report["blockers"].append(
                f"MODE_NOT_SUPPORTED_WITHOUT_ACTIVATION_BACKEND:{config.audit_mode}"
            )

        report["status"] = "ready" if not report["blockers"] else "blocked"
        return report

    def declared_runtime_ids(self) -> list[str]:
        """Runtime identifiers the kernel itself declared (no invented ids)."""
        kernel = self._kernel()
        capabilities = kernel.capabilities()
        for item in capabilities:
            if isinstance(item, dict) and item.get("component_id") == "agent.runtime":
                metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
                ids: list[str] = []
                if isinstance(metadata.get("runtime_id"), str):
                    ids.append(metadata["runtime_id"])
                for model_id in metadata.get("model_ids") or []:
                    if isinstance(model_id, str):
                        ids.append(model_id)
                return list(dict.fromkeys(ids))
        return []

    # ------------------------------------------------------------------ #
    # execution
    # ------------------------------------------------------------------ #
    def build_audit_request(
        self, config: InvestigationRunConfig, *, request_id: str
    ) -> WardenAuditRequest:
        """Map the run config onto WARDEN's public request contract.

        AgentGuard-only study fields (budgets, partition, source commit) stay
        out of the kernel request by construction.
        """
        metadata: dict[str, Any] = dict(config.metadata)
        if config.random_seed is not None:
            metadata["seed"] = config.random_seed
        return WardenAuditRequest(
            request_id=request_id,
            contract_id=config.contract_id,
            model_id=config.model_id,
            runtime_id=config.runtime_id,
            mode=config.audit_mode,
            capture_targets=[target.model_dump() for target in config.capture_targets],
            metadata=metadata,
            scenario_id=config.scenario_id,
            start_run=True,
            wait_for_completion=True,
            timeout_seconds=min(config.time_budget, 110.0),
            poll_interval_seconds=2.0,
        )

    def execute(
        self,
        config: InvestigationRunConfig,
        *,
        request_id: str | None = None,
        skip_resolution: bool = False,
    ) -> ObservationRunRecord:
        """Resolve, execute and record one standardized run.

        The default ``request_id`` is derived from the run configuration so the
        same fixed configuration replays idempotently; pass a distinct
        ``request_id`` for protocol repetitions that must create new runs.
        Ambiguous external submissions stay ambiguous — the lifecycle service
        never blindly resubmits.
        """
        resolution = {"status": "skipped", "blockers": []} if skip_resolution else self.resolve(config)
        effective_request_id = request_id or f"obs-{config.run_config_id}"
        record = ObservationRunRecord(
            run_config_id=config.run_config_id,
            request_id=effective_request_id,
            model_id=config.model_id,
            runtime_id=config.runtime_id,
            scenario_id=config.scenario_id,
            contract_id=config.contract_id,
            audit_mode=config.audit_mode,
            execution_status="blocked",
        )
        if resolution["status"] != "ready":
            record.blockers = list(resolution["blockers"])
            record.limitations.append(
                "Execution was not attempted: the run configuration did not "
                "resolve against the live kernel."
            )
            return self._finalize(record, config, None)

        kernel = self._kernel()
        lifecycle = WardenLifecycleService(
            kernel, experiences=self._experiences, workflows=self._workflows,
        )
        audit_request = self.build_audit_request(config, request_id=effective_request_id)
        started = time.monotonic()
        try:
            result = lifecycle.execute(audit_request, approval_valid=True)
        except Exception as exc:  # noqa: BLE001 - record the failure, never retry blindly
            record.execution_status = "failed"
            record.blockers.append(f"EXECUTION_ERROR:{type(exc).__name__}")
            record.limitations.append(
                "The lifecycle service raised during execution; the durable "
                "journal holds the ambiguous-submission state if any."
            )
            return self._finalize(record, config, time.monotonic() - started)
        record.elapsed_seconds = round(time.monotonic() - started, 3)
        record.execution_status = str(result.get("status", "unknown"))
        plan = result.get("plan") if isinstance(result.get("plan"), Mapping) else {}
        run = result.get("run") if isinstance(result.get("run"), Mapping) else {}
        record.plan_id = plan.get("plan_id") if isinstance(plan.get("plan_id"), str) else None
        record.run_id = run.get("run_id") if isinstance(run.get("run_id"), str) else None
        record.evidence_refs = WardenLifecycleService._evidence_refs(run) if run else []
        entry = self._workflows.get(effective_request_id) or {}
        record.submitted_at = entry.get("created_at")
        record.completed_at = entry.get("updated_at")
        if record.run_id and record.execution_status == "completed":
            record.data_source = "warden_observation"
        elif record.plan_id:
            record.data_source = "warden_plan_only"
        if config.audit_mode == "observation":
            record.limitations.append(
                "Observation-only run: no causal intervention was requested or "
                "performed; results stay observational."
            )
        if result.get("idempotent_replay"):
            record.limitations.append("Idempotent replay of an already-recorded workflow.")
        return self._finalize(record, config, record.elapsed_seconds)

    def _finalize(
        self,
        record: ObservationRunRecord,
        config: InvestigationRunConfig,
        elapsed: float | None,
    ) -> ObservationRunRecord:
        """Persist config and record as content-addressed artifacts."""
        config_artifact = self._artifacts.put(
            config.model_dump_json(indent=2).encode("utf-8"),
            name=f"run-config-{config.run_config_id}.json",
            description="Versioned investigation run configuration",
            tags=["run-config", config.audit_mode],
            metadata={
                "run_config_id": config.run_config_id,
                "data_partition": config.data_partition,
            },
            media_type="application/json",
        )
        record.run_config_artifact_id = config_artifact.artifact_ref.artifact_id
        record_artifact = self._artifacts.put(
            record.model_dump_json(indent=2).encode("utf-8"),
            name=f"observation-run-{record.request_id}.json",
            description="Standardized observation run record",
            tags=["observation-run", record.execution_status],
            metadata={"request_id": record.request_id,
                      "run_config_id": config.run_config_id},
            media_type="application/json",
        )
        record.record_artifact_id = record_artifact.artifact_ref.artifact_id
        return record
