"""Bounded end-to-end orchestration against the authoritative WARDEN kernel.

This module owns only upper-layer lifecycle coordination. Probe/SAE analysis,
activation capture, causal execution, and low-level evidence generation remain in WARDEN.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from typing import Any

from agentguard.adapters.warden import WardenKernelClient, WardenKernelError
from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.memory.store import ExperienceStore
from agentguard.services.warden_workflow_store import WardenWorkflowStore

_TERMINAL = {"completed", "blocked", "failed", "cancelled"}
_ACTIVE = {"planned", "queued", "running"}
_KNOWN = _TERMINAL | _ACTIVE


class WardenLifecycleService:
    """Preflight, submit, optionally poll, and safely record WARDEN audit runs.

    `sleep` and `monotonic` are injectable so polling behavior can be tested without
    wall-clock delays. The service never interprets `blocked`, `failed`, `unknown`,
    or missing evidence as a negative detection result.
    """

    def __init__(
        self,
        client: WardenKernelClient,
        *,
        experiences: ExperienceStore | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        workflows: WardenWorkflowStore | None = None,
    ) -> None:
        self.client = client
        self.experiences = experiences
        self.workflows = workflows
        self._sleep = sleep
        self._monotonic = monotonic

    def execute(
        self,
        request: WardenAuditRequest,
        *,
        approval_valid: bool = False,
    ) -> dict[str, Any]:
        """Execute idempotently when a durable workflow journal is configured.

        The request_id is the idempotency key. Completed responses are replayed;
        known running jobs can be resumed; ambiguous external submissions are never
        repeated automatically because WARDEN may have accepted the first request.
        """
        if self.workflows is None:
            return self._execute_new(request, approval_valid=approval_valid, workflow_id=None)

        payload = request.model_dump(mode="json")
        entry, created = self.workflows.begin(request.request_id, payload)
        if not created:
            saved_context = entry.get("context") or {}
            saved_run = saved_context.get("run") or {}
            if entry.get("status") == "interrupted" and saved_run.get("run_id"):
                return self._resume_existing(request, entry)
            if entry.get("result") is not None and entry.get("status") not in {"timed_out", "run_submitted", "polling", "plan_created"}:
                replay = dict(entry["result"])
                replay["idempotent_replay"] = True
                replay["workflow_revision"] = entry["revision"]
                return replay
            if entry.get("status") in {
                "run_submitted", "polling", "timed_out", "plan_created",
                "submission_uncertain", "run_submitting"
            }:
                return self._resume_existing(request, entry)
            if entry.get("status") not in {"created", "preflighting", "interrupted"}:
                return self._reconciliation_report(entry)

        self.workflows.update(request.request_id, "preflighting", context={"stage": "preflighting"})
        try:
            result = self._execute_new(request, approval_valid=approval_valid, workflow_id=request.request_id)
        except Exception as exc:
            current = self.workflows.get(request.request_id) or {}
            current_context = current.get("context") or {}
            stage = current_context.get("stage", current.get("status", "unknown"))
            uncertain = stage in {"plan_submitting", "run_submitting"}
            saved_run = current_context.get("run") or {}
            if saved_run.get("run_id"):
                retry_state = "run_submitted"
            elif uncertain:
                retry_state = "submission_uncertain"
            elif stage == "plan_created":
                retry_state = "plan_created"
            else:
                retry_state = "interrupted"
            self.workflows.update(
                request.request_id,
                retry_state,
                context={"stage": retry_state} if not uncertain else None,
                error_code=getattr(exc, "code", type(exc).__name__),
                error_message=str(exc)[:300],
                detail={"exception_type": type(exc).__name__, "external_submission_may_have_succeeded": uncertain},
            )
            raise
        result_status = str(result.get("status", "unknown"))
        if result_status == "timed_out":
            stored_status = "timed_out"
        elif result_status in _ACTIVE and isinstance(result.get("run"), Mapping) and result["run"].get("run_id"):
            # A non-waiting call is not terminal: later retries should reconcile the same run.
            stored_status = "run_submitted"
        else:
            stored_status = "finished"
        context = {"stage": stored_status}
        for key in ("preflight", "plan", "run"):
            if result.get(key) is not None:
                context[key] = result[key]
        self.workflows.update(request.request_id, stored_status, context=context, result=result, detail={"result_status": result_status})
        result = dict(result)
        result["workflow_id"] = request.request_id
        result["workflow_revision"] = (self.workflows.get(request.request_id) or {}).get("revision")
        return result

    def _execute_new(
        self,
        request: WardenAuditRequest,
        *,
        approval_valid: bool = False,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a WARDEN lifecycle with bounded polling and evidence-gated memory."""
        if request.mode == "defense_evaluation" and not approval_valid:
            return self._report(
                status="blocked",
                preflight={"status": "blocked", "blockers": ["OPERATOR_APPROVAL_REQUIRED"]},
                note="Defense evaluation requires explicit AgentGuard approval; WARDEN was not contacted.",
            )

        preflight = self.preflight(request)
        if preflight["status"] != "ready":
            return self._report(
                status="blocked" if preflight["status"] == "blocked" else "unavailable",
                preflight=preflight,
                note="Preflight did not pass; no audit plan was submitted.",
            )

        if workflow_id and self.workflows:
            self.workflows.update(workflow_id, "plan_submitting", context={"stage": "plan_submitting", "preflight": preflight}, detail={"operation": "create_audit_plan"})
        plan = self.client.create_audit_plan(request.to_warden_payload())
        if workflow_id and self.workflows:
            self.workflows.update(workflow_id, "plan_created", context={"stage": "plan_created", "preflight": preflight, "plan": plan}, detail={"plan_id": plan.get("plan_id"), "plan_status": plan.get("status")})
        plan_status = self._status(plan)
        if plan_status == "blocked":
            return self._report(
                status="blocked", preflight=preflight, plan=plan,
                blockers=self._strings(plan.get("blockers")),
                note="WARDEN blocked the audit plan. This is not a negative detection result.",
            )
        if plan_status != "planned":
            return self._report(
                status="unknown", preflight=preflight, plan=plan,
                note=f"Unexpected WARDEN plan status: {plan_status}; execution was not started.",
            )
        if not request.start_run:
            return self._report(
                status="planned", preflight=preflight, plan=plan,
                note="Plan created only; no run was submitted.",
            )

        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise WardenKernelError(
                "WARDEN planned response is missing plan_id", code="WARDEN_BAD_RESPONSE"
            )
        if workflow_id and self.workflows:
            self.workflows.update(workflow_id, "run_submitting", context={"stage": "run_submitting", "preflight": preflight, "plan": plan}, detail={"operation": "start_run", "plan_id": plan_id})
        run = self.client.start_run(plan_id)
        if workflow_id and self.workflows:
            self.workflows.update(workflow_id, "run_submitted", context={"stage": "run_submitted", "preflight": preflight, "plan": plan, "run": run}, detail={"run_id": run.get("run_id"), "run_status": run.get("status")})
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise WardenKernelError(
                "WARDEN run response is missing run_id", code="WARDEN_BAD_RESPONSE"
            )
        run_status = self._status(run)
        if run_status not in _KNOWN:
            return self._report(
                status="unknown", preflight=preflight, plan=plan, run=run,
                note=f"Unexpected WARDEN run status: {run_status}; no experience was recorded.",
            )
        if request.wait_for_completion and run_status in _ACTIVE:
            if workflow_id and self.workflows:
                self.workflows.update(workflow_id, "polling", context={"stage": "polling", "preflight": preflight, "plan": plan, "run": run}, detail={"run_id": run.get("run_id")})
            run, run_status, timed_out = self._poll(run, request.timeout_seconds, request.poll_interval_seconds)
            if timed_out:
                return self._report(
                    status="timed_out", preflight=preflight, plan=plan, run=run,
                    note="Polling deadline elapsed; WARDEN may still be running. No experience was recorded.",
                )

        experience = self._record_experience(request, run) if run_status == "completed" else None
        if run_status == "completed":
            claim_status = (
                "awaiting_review"
                if request.mode != "observation" and self._evidence_refs(run)
                else "not_assessed"
            )
        else:
            claim_status = "not_assessed"
        note = {
            "completed": "WARDEN reports completion; causal claims still require evidence and methodological review.",
            "blocked": "WARDEN blocked execution; this is not a negative detection result.",
            "failed": "WARDEN run failed; this is not a negative detection result.",
            "cancelled": "WARDEN run was cancelled; no conclusion is drawn.",
            "queued": "WARDEN accepted the run; execution has not completed.",
            "running": "WARDEN run is still active.",
            "planned": "WARDEN plan exists; execution has not completed.",
        }.get(run_status, "WARDEN status is not conclusive.")
        return self._report(
            status=run_status, preflight=preflight, plan=plan, run=run,
            experience=experience.model_dump(mode="json") if experience else None,
            causal_claim_status=claim_status, note=note,
        )

    def _resume_existing(self, request: WardenAuditRequest, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Resume a known plan/run without creating a duplicate remote submission."""
        if self.workflows is None:
            return self._reconciliation_report(entry)
        request_id = str(entry.get("request_id") or request.request_id)
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        preflight = dict(context.get("preflight") or {"status": "ready", "blockers": []})
        plan = dict(context.get("plan") or {})
        run = dict(context.get("run") or {})
        state = str(entry.get("status") or "unknown")
        if state == "interrupted" and run.get("run_id"):
            state = "run_submitted"

        if state in {"submission_uncertain", "run_submitting"} and context.get("stage") == "run_submitting":
            plan_id = plan.get("plan_id")
            if isinstance(plan_id, str) and plan_id.strip():
                try:
                    remote_plan = self.client.get_audit_plan(plan_id)
                    remote_request = remote_plan.get("request")
                    expected = request.to_warden_payload()
                    if (
                        remote_plan.get("plan_id") != plan_id
                        or not isinstance(remote_request, Mapping)
                        or set(remote_request) - set(expected)
                    ):
                        return self._reconciliation_report(entry)
                    try:
                        normalized = WardenAuditRequest.model_validate(remote_request).to_warden_payload()
                    except ValueError:
                        return self._reconciliation_report(entry)
                    if self.workflows.canonical_hash(normalized) != self.workflows.canonical_hash(expected):
                        return self._reconciliation_report(entry)
                    matches = [
                        item for item in self.client.list_runs()
                        if item.get("plan_id") == plan_id
                    ]
                except WardenKernelError:
                    return self._reconciliation_report(entry)
                if (
                    len(matches) == 1
                    and isinstance(matches[0].get("run_id"), str)
                    and matches[0]["run_id"].strip()
                ):
                    run = dict(matches[0])
                    self.workflows.update(
                        request_id, "run_submitted",
                        context={"stage": "run_submitted", "run": run},
                        detail={"reconciled": True, "run_id": run["run_id"]},
                    )
                    state = "run_submitted"

        if state == "plan_created":
            if not request.start_run:
                result = self._report(status="planned", preflight=preflight, plan=plan, note="Plan was created earlier; no run was submitted.")
                self.workflows.update(request_id, "finished", context={"stage": "finished"}, result=result, detail={"recovered": True})
                return {**result, "workflow_id": request_id, "recovered": True}
            plan_id = plan.get("plan_id")
            if not isinstance(plan_id, str) or not plan_id.strip():
                return self._reconciliation_report(entry)
            self.workflows.update(request_id, "run_submitting", context={"stage": "run_submitting"}, detail={"recovered_from": "plan_created", "plan_id": plan_id})
            try:
                run = self.client.start_run(plan_id)
            except Exception as exc:
                self.workflows.update(request_id, "submission_uncertain", error_code=getattr(exc, "code", type(exc).__name__), error_message=str(exc)[:300], detail={"external_submission_may_have_succeeded": True})
                return self._reconciliation_report(self.workflows.get(request_id) or entry)
            run_id = run.get("run_id")
            if not isinstance(run_id, str) or not run_id.strip():
                self.workflows.update(request_id, "submission_uncertain", context={"run": run}, error_code="WARDEN_BAD_RESPONSE", error_message="Run response missing run_id")
                return self._reconciliation_report(self.workflows.get(request_id) or entry)
            self.workflows.update(request_id, "run_submitted", context={"stage": "run_submitted", "preflight": preflight, "plan": plan, "run": run}, detail={"recovered": True, "run_id": run_id})
            state = "run_submitted"

        if state in {"run_submitted", "polling", "timed_out"}:
            run_id = run.get("run_id")
            if not isinstance(run_id, str) or not run_id.strip():
                return self._reconciliation_report(entry)
            try:
                run = self.client.get_run(run_id)
            except WardenKernelError as exc:
                return self._report(status="unavailable", preflight=preflight, plan=plan, run=run, note=f"Could not reconcile WARDEN run ({exc.code}); no new run was submitted.")
            run_status = self._status(run)
            if run_status not in _KNOWN:
                result = self._report(status="unknown", preflight=preflight, plan=plan, run=run, note="Recovered WARDEN run returned an unknown status; no experience was recorded.")
                self.workflows.update(request_id, "finished", context={"stage": "finished", "preflight": preflight, "plan": plan, "run": run}, result=result, detail={"recovered": True, "run_status": run_status})
                return {**result, "workflow_id": request_id, "recovered": True}
            if request.wait_for_completion and run_status in _ACTIVE:
                self.workflows.update(request_id, "polling", context={"stage": "polling", "preflight": preflight, "plan": plan, "run": run}, detail={"recovered": True, "run_id": run_id})
                run, run_status, timed_out = self._poll(run, request.timeout_seconds, request.poll_interval_seconds)
                if timed_out:
                    result = self._report(status="timed_out", preflight=preflight, plan=plan, run=run, note="Polling deadline elapsed during recovery; WARDEN may still be running. No experience was recorded.")
                    self.workflows.update(request_id, "timed_out", context={"stage": "timed_out", "preflight": preflight, "plan": plan, "run": run}, result=result, detail={"recovered": True})
                    return {**result, "workflow_id": request_id, "recovered": True}
            experience = self._record_experience(request, run) if run_status == "completed" else None
            claim_status = (
                "awaiting_review"
                if request.mode != "observation"
                and run_status == "completed"
                and self._evidence_refs(run)
                else "not_assessed"
            )
            note = {
                "completed": "Recovered WARDEN run completed; causal claims still require evidence and methodological review.",
                "blocked": "WARDEN blocked the recovered run; this is not a negative detection result.",
                "failed": "Recovered WARDEN run failed; this is not a negative detection result.",
                "cancelled": "Recovered WARDEN run was cancelled; no conclusion is drawn.",
                "queued": "Recovered WARDEN run is queued.",
                "running": "Recovered WARDEN run is still active.",
                "planned": "Recovered WARDEN run remains planned.",
            }.get(run_status, "Recovered WARDEN status is not conclusive.")
            result = self._report(status=run_status, preflight=preflight, plan=plan, run=run, experience=experience.model_dump(mode="json") if experience else None, causal_claim_status=claim_status, note=note)
            if run_status in _ACTIVE and request.wait_for_completion:
                final_state = "timed_out"
            elif run_status in _ACTIVE:
                final_state = "run_submitted"
            else:
                final_state = "finished"
            self.workflows.update(request_id, final_state, context={"stage": final_state, "preflight": preflight, "plan": plan, "run": run}, result=result, detail={"recovered": True, "run_status": run_status})
            return {**result, "workflow_id": request_id, "recovered": True}

        return self._reconciliation_report(entry)

    @staticmethod
    def _reconciliation_report(entry: Mapping[str, Any]) -> dict[str, Any]:
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        return {
            "kernel": "WARDEN",
            "status": "reconciliation_required",
            "workflow_id": entry.get("request_id"),
            "workflow_state": entry.get("status"),
            "plan_id": (context.get("plan") or {}).get("plan_id") if isinstance(context.get("plan"), Mapping) else None,
            "run_id": (context.get("run") or {}).get("run_id") if isinstance(context.get("run"), Mapping) else None,
            "causal_claim_status": "not_assessed",
            "note": "The previous submission may have reached WARDEN but its outcome is ambiguous. No duplicate submission was made; reconcile the recorded request with WARDEN before retrying.",
        }

    def preflight(self, request: WardenAuditRequest) -> dict[str, Any]:
        """Check health, contracts, and explicitly requested Probe/SAE resources."""
        health = self.client.health()
        if health.get("status") != "ok":
            return {"status": "unavailable", "blockers": ["WARDEN_HEALTH_NOT_OK"], "health": health}
        if health.get("api_version") != "v1":
            return {"status": "blocked", "blockers": ["WARDEN_API_VERSION_INCOMPATIBLE"], "health": health}

        capabilities = self.client.capabilities()
        contracts = self.client.contracts()
        blockers: list[str] = []
        if request.contract_id not in contracts:
            blockers.append(f"THREAT_CONTRACT_NOT_AVAILABLE:{request.contract_id}")

        requested_resources: dict[str, list[dict[str, Any]]] = {}
        for kind, resource_id in (("probe", request.probe_id), ("sae", request.sae_id)):
            if not resource_id:
                continue
            resources = self.client.resources(kind)
            requested_resources[kind] = resources
            match = next((item for item in resources if item.get("resource_id") == resource_id), None)
            if match is None:
                blockers.append(f"{kind.upper()}_RESOURCE_NOT_FOUND:{resource_id}")
            elif match.get("state") != "ready":
                blockers.append(f"{kind.upper()}_RESOURCE_NOT_READY:{resource_id}:{match.get('state', 'unknown')}")

        return {
            "status": "blocked" if blockers else "ready",
            "blockers": blockers,
            "health": health,
            "capabilities": capabilities,
            "contract_count": len(contracts),
            "requested_resources": {
                kind: [
                    {"resource_id": item.get("resource_id"), "state": item.get("state"), "version": item.get("version")}
                    for item in items
                ]
                for kind, items in requested_resources.items()
            },
        }

    def _poll(
        self, initial_run: dict[str, Any], timeout_seconds: float, interval_seconds: float
    ) -> tuple[dict[str, Any], str, bool]:
        run = initial_run
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise WardenKernelError("WARDEN active run response is missing run_id", code="WARDEN_BAD_RESPONSE")
        deadline = self._monotonic() + float(timeout_seconds)
        delay = float(interval_seconds)
        while self._monotonic() < deadline:
            remaining = deadline - self._monotonic()
            self._sleep(min(delay, max(0.0, remaining)))
            run = self.client.get_run(run_id)
            status = self._status(run)
            if status not in _KNOWN:
                return run, "unknown", False
            if status not in _ACTIVE:
                return run, status, False
            delay = min(delay * 1.6, 5.0)
        return run, self._status(run), True

    def _record_experience(
        self, request: WardenAuditRequest, run: Mapping[str, Any]
    ) -> ExperienceRecord | None:
        """Persist only completed runs with explicit evidence refs and usable labels.

        Records are deliberately observational, unverified, inconclusive, and never
        auto-recommended. A completed run alone is not proof of a causal mechanism.
        """
        refs = self._evidence_refs(run)
        if self.experiences is None or not refs or not request.scenario_id or not request.risk_type:
            return None
        run_id = str(run.get("run_id") or "")
        if not run_id:
            return None
        experience_id = "exp-warden-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:20]
        existing = self.experiences.get(experience_id)
        if existing is not None:
            return existing
        health_version = "unknown"
        try:
            health_version = str(self.client.health().get("version") or "unknown")
        except WardenKernelError:
            pass
        seed_value = request.metadata.get("seed")
        seed = seed_value if isinstance(seed_value, int) and not isinstance(seed_value, bool) else None
        record = ExperienceRecord(
            experience_id=experience_id,
            scenario_id=request.scenario_id,
            case_family=str(request.metadata.get("case_family") or "unspecified"),
            risk_type=request.risk_type,
            hypothesis_summary="WARDEN completed an audit run; causal interpretation is pending review.",
            action_type="warden_audit_observation",
            action_summary="Completed WARDEN audit; no defense effectiveness is inferred.",
            outcome="inconclusive",
            evidence_grade="observational",
            verification_status="unverified",
            repetitions=1,
            evidence_refs=refs,
            validation_notes=["Automatically recorded from a completed WARDEN run; human/methodological review required."],
            metrics={},
            model_version=request.model_id,
            code_version=health_version,
            seed=seed,
            allow_auto_recommendation=False,
            tags=["warden", "audit-observation", "requires-review"],
        )
        return self.experiences.add(record)

    @classmethod
    def _evidence_refs(cls, run: Mapping[str, Any]) -> list[str]:
        """Extract only explicit evidence/artifact references from known response fields."""
        refs: list[str] = []
        for key in ("evidence_refs", "artifact_refs"):
            value = run.get(key)
            if isinstance(value, list):
                refs.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
        for key in ("evidence", "evidence_bundle", "artifacts", "result"):
            value = run.get(key)
            if isinstance(value, Mapping):
                artifacts = value.get("artifacts")
                if isinstance(artifacts, list):
                    for artifact in artifacts:
                        if isinstance(artifact, Mapping):
                            ref = artifact.get("uri") or artifact.get("artifact_id")
                            if isinstance(ref, str) and ref.strip():
                                refs.append(ref.strip())
                for ref_key in ("evidence_refs", "artifact_refs"):
                    items = value.get(ref_key)
                    if isinstance(items, list):
                        refs.extend(item.strip() for item in items if isinstance(item, str) and item.strip())
                for ref_key in ("artifact_id", "evidence_id", "uri"):
                    item = value.get(ref_key)
                    if isinstance(item, str) and item.strip():
                        refs.append(item.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        refs.append(item.strip())
                    elif isinstance(item, Mapping):
                        for ref_key in ("artifact_id", "evidence_id", "ref", "uri"):
                            ref = item.get(ref_key)
                            if isinstance(ref, str) and ref.strip():
                                refs.append(ref.strip())
        return list(dict.fromkeys(refs))

    @staticmethod
    def _status(value: Mapping[str, Any]) -> str:
        raw = value.get("status")
        return raw.strip().lower() if isinstance(raw, str) and raw.strip() else "unknown"

    @staticmethod
    def _strings(value: Any) -> list[str]:
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    @staticmethod
    def _report(
        *, status: str, preflight: dict[str, Any], plan: dict[str, Any] | None = None,
        run: dict[str, Any] | None = None, blockers: list[str] | None = None,
        experience: dict[str, Any] | None = None, causal_claim_status: str = "not_assessed",
        note: str,
    ) -> dict[str, Any]:
        return {
            "kernel": "WARDEN",
            "status": status,
            "preflight": preflight,
            "blockers": blockers or [],
            "plan": plan,
            "run": run,
            "experience": experience,
            "causal_claim_status": causal_claim_status,
            "note": note,
        }
