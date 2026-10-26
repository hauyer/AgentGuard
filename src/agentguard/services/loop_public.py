"""Public investigation-loop service layer (E2).

Bridges the durable loop registry to the D3 loop service. Creation validates
the base investigation and runs the real loop; reads project persisted report
artifacts without inventing fields. Route-level authorization (operator or
approval credential) gates creation; this service never trusts client role
claims.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from agentguard.artifacts.local_store import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    LocalArtifactStore,
)
from agentguard.contracts.investigation_loop import (
    InvestigationLoopBudget,
    InvestigationLoopReport,
)
from agentguard.contracts.loop_public import (
    InvestigationLoopCreateRequest,
    InvestigationLoopListResponse,
    InvestigationLoopReconciliationResponse,
    InvestigationLoopView,
    LoopBudgetResponse,
    LoopDecisionsResponse,
    LoopDecisionView,
    LoopEvidenceChangesResponse,
)
from agentguard.services.investigation_loop import (
    InvestigationLoopError,
    InvestigationLoopService,
)
from agentguard.services.loop_registry import InvestigationLoopRegistry


class LoopServiceError(ValueError):
    """Public loop failure mapped to a specific API error by the route."""


class PublicInvestigationLoopService:
    """Create and project investigation loops over the D3 service."""

    def __init__(
        self,
        *,
        loop: InvestigationLoopService,
        registry: InvestigationLoopRegistry,
        artifacts: LocalArtifactStore,
    ) -> None:
        self.loop = loop
        self.registry = registry
        self.artifacts = artifacts

    @staticmethod
    def _request_payload(request: InvestigationLoopCreateRequest) -> dict[str, Any]:
        """Return every behavior-bearing request field for idempotency checks.

        ``authorization_ref`` is included only when supplied so registrations
        created by earlier releases with the default ``null`` remain replayable.
        """
        payload: dict[str, Any] = {
            "base_request_id": request.base_request_id,
            "strategy": request.strategy,
            "max_actions": request.max_actions,
            "max_seconds": request.max_seconds,
            "max_model_calls": request.max_model_calls,
            "no_new_evidence_rounds": request.no_new_evidence_rounds,
            "data_partition": request.data_partition,
        }
        if request.authorization_ref is not None:
            payload["authorization_ref"] = request.authorization_ref
        return payload

    # ------------------------------------------------------------------ #
    # creation (authorization enforced by the route, not here)
    # ------------------------------------------------------------------ #
    def create(
        self, request: InvestigationLoopCreateRequest, *, authorization_valid: bool,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise LoopServiceError("LOOP_IDEMPOTENCY_KEY_REQUIRED")
        return self.create_with_idempotency_key(
            request,
            idempotency_key=idempotency_key.strip(),
            authorization_valid=authorization_valid,
        )

    def create_with_idempotency_key(
        self, request: InvestigationLoopCreateRequest, *,
        idempotency_key: str, authorization_valid: bool,
    ) -> dict[str, Any]:
        """Register-then-run under the caller's idempotency key.

        The registration row is inserted first (pre-run) so concurrent or
        retried creations collapse onto one loop; identical payloads return
        the existing loop, conflicting payloads raise.
        """
        if not authorization_valid:
            raise LoopServiceError("LOOP_AUTHORIZATION_REQUIRED")
        entry = self.loop.workflows.get(request.base_request_id)
        if entry is None:
            raise LoopServiceError("BASE_INVESTIGATION_NOT_FOUND")
        if entry.get("status") not in {"finished", "completed"}:
            raise LoopServiceError("BASE_INVESTIGATION_NOT_FINISHED")
        request_payload = self._request_payload(request)
        loop_id = f"loop-{request.base_request_id}-{uuid.uuid4().hex[:10]}"
        now = datetime.now(UTC).isoformat()
        registration, _ = self.registry.register(
            loop_id=loop_id,
            idempotency_key=idempotency_key,
            base_request_id=request.base_request_id,
            strategy=request.strategy,
            phase="CREATED",
            stop_reason=None,
            request_payload=request_payload,
            report_artifact_id="pending",
            created_at=now,
        )

        # The key lock spans the entire execution. If another process owns it,
        # return its durable state without starting a duplicate loop.
        with self.loop.workflows.execution_lock(
            f"public-investigation-loop:{idempotency_key}"
        ) as acquired:
            if not acquired:
                return self.get_view(registration["loop_id"])

            current = self.registry.get(registration["loop_id"])
            if current is None:
                raise LoopServiceError("LOOP_REGISTRATION_LOST")
            execution_state = current["execution_state"]
            if execution_state == "finished":
                return self.get_view(registration["loop_id"])
            if execution_state in {"running", "unknown"}:
                # A prior process may have spent budget or submitted an audit.
                # Keep the outcome unknown and require reconciliation; never
                # infer completion or submit the action again.
                self.registry.update_outcome(
                    registration["loop_id"], phase="BLOCKED",
                    stop_reason="reconciliation_required",
                    report_artifact_id="pending",
                    execution_state="unknown",
                    updated_at=datetime.now(UTC).isoformat(),
                )
                return self.get_view(registration["loop_id"])
            if execution_state != "not_started":
                return self.get_view(registration["loop_id"])

            # This durable marker separates a never-started registration from
            # a process that may have been interrupted mid-run.
            self.registry.update_outcome(
                registration["loop_id"], phase="RUNNING", stop_reason=None,
                report_artifact_id="pending", execution_state="running",
                updated_at=datetime.now(UTC).isoformat(),
            )
            return self._run_registered_loop(registration["loop_id"], request)

    def _run_registered_loop(
        self, loop_id: str, request: InvestigationLoopCreateRequest,
    ) -> dict[str, Any]:
        budget = InvestigationLoopBudget(
            max_actions=request.max_actions,
            max_seconds=request.max_seconds,
            max_model_calls=request.max_model_calls,
            no_new_evidence_rounds=request.no_new_evidence_rounds,
        )
        try:
            report = self.loop.run(
                request.base_request_id, strategy=request.strategy, budget=budget,
                registration_id=loop_id,
            )
        except InvestigationLoopError as exc:
            self.registry.update_outcome(
                loop_id, phase="BLOCKED", stop_reason="execution_error",
                report_artifact_id="failed", execution_state="failed",
                updated_at=datetime.now(UTC).isoformat(),
            )
            raise LoopServiceError("LOOP_EXECUTION_FAILED") from exc
        except Exception as exc:
            self.registry.update_outcome(
                loop_id, phase="BLOCKED", stop_reason="execution_error",
                report_artifact_id="failed", execution_state="failed",
                updated_at=datetime.now(UTC).isoformat(),
            )
            raise LoopServiceError("LOOP_EXECUTION_FAILED") from exc
        self.registry.update_outcome(
            loop_id, phase=report.phase, stop_reason=report.stop_reason,
            report_artifact_id=report.report_artifact_id or "",
            execution_state="finished",
            updated_at=datetime.now(UTC).isoformat(),
        )
        return self.get_view(loop_id)

    def reconcile_loop(
        self,
        loop_id: str,
        *,
        reconcile_workflow=None,
    ) -> dict[str, Any]:
        """Recover local facts and query only already-registered WARDEN operations."""
        registration = self.registry.get(loop_id)
        if registration is None:
            raise LoopServiceError("LOOP_NOT_FOUND")
        lock_suffix = registration.get("idempotency_key") or loop_id
        with self.loop.workflows.execution_lock(
            f"public-investigation-loop:{lock_suffix}"
        ) as acquired:
            if not acquired:
                return InvestigationLoopReconciliationResponse(
                    loop_id=loop_id,
                    status="reconciliation_required",
                    report_reconstructed=False,
                    blockers=["LOOP_LOCKED"],
                ).model_dump(mode="json")
            registration = self.registry.get(loop_id)
            if registration is None:
                raise LoopServiceError("LOOP_NOT_FOUND")
            report, report_artifact_id, ambiguous_report = self._recoverable_report(registration)
            if report is not None:
                self.registry.update_outcome(
                    loop_id, phase=report.phase, stop_reason=report.stop_reason,
                    report_artifact_id=report_artifact_id or registration["report_artifact_id"],
                    execution_state="finished", updated_at=datetime.now(UTC).isoformat(),
                )
                return InvestigationLoopReconciliationResponse(
                    loop_id=loop_id,
                    status="not_needed" if registration["execution_state"] == "finished" else "reconciled",
                    report_reconstructed=True,
                ).model_dump(mode="json")
            if registration["execution_state"] not in {"running", "unknown"}:
                return InvestigationLoopReconciliationResponse(
                    loop_id=loop_id,
                    status="reconciliation_required",
                    report_reconstructed=False,
                    blockers=[
                        "LOOP_REPORT_ARTIFACT_AMBIGUOUS" if ambiguous_report
                        else "LOOP_REPORT_ARTIFACT_UNAVAILABLE",
                    ],
                ).model_dump(mode="json")

            matches = self.registry.workflows_for_loop_base(registration["base_request_id"])
            matches = [
                item for item in matches
                if re.fullmatch(
                    re.escape(registration["base_request_id"]) + r"-audit-r[1-9][0-9]*",
                    item["request_id"],
                )
            ]
            if not matches:
                blockers = ["LOOP_REPORT_CHECKPOINT_UNAVAILABLE", "NO_RECORDED_AUDIT_WORKFLOW"]
                reconciled = []
            elif reconcile_workflow is None:
                blockers = ["WARDEN_NOT_CONFIGURED", "LOOP_REPORT_CHECKPOINT_UNAVAILABLE"]
                reconciled = []
            else:
                reconciled = []
                blockers = ["LOOP_REPORT_CHECKPOINT_UNAVAILABLE"]
                for workflow in matches:
                    result = reconcile_workflow(workflow["request_id"])
                    workflow_blockers = result.get("blockers")
                    if isinstance(workflow_blockers, list):
                        blockers.extend(
                            value for value in workflow_blockers
                            if isinstance(value, str) and value
                        )
                    reconciled.append({
                        "request_id": workflow["request_id"],
                        "workflow_status": str(result.get("workflow_status") or result.get("status") or "unknown"),
                        "remote_status": str(result.get("status") or "unknown"),
                        "plan_id": result.get("plan_id") or workflow.get("plan_id"),
                        "run_id": result.get("run_id") or workflow.get("run_id"),
                    })
            self.registry.update_outcome(
                loop_id, phase="BLOCKED", stop_reason="reconciliation_required",
                report_artifact_id="pending", execution_state="unknown",
                updated_at=datetime.now(UTC).isoformat(),
            )
            return InvestigationLoopReconciliationResponse(
                loop_id=loop_id,
                status="reconciliation_required",
                report_reconstructed=False,
                workflows=reconciled,
                blockers=list(dict.fromkeys(blockers)),
            ).model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def get_view(self, loop_id: str) -> dict[str, Any]:
        registration = self.registry.get(loop_id)
        if registration is None:
            raise LoopServiceError("LOOP_NOT_FOUND")
        view = InvestigationLoopView(
            loop_id=registration["loop_id"],
            base_request_id=registration["base_request_id"],
            strategy=registration["strategy"],
            phase=registration["phase"],
            stop_reason=registration["stop_reason"],
            report_artifact_id=registration["report_artifact_id"],
            created_at=registration["created_at"],
            updated_at=registration["updated_at"],
        )
        report = self._load_report(registration["report_artifact_id"])
        if report is not None:
            view.rounds_executed = report.metrics.rounds_executed
            view.created_audit_request_ids = list(report.created_audit_request_ids)
            view.budget = report.budget.model_dump(mode="json")
            view.metrics = report.metrics.model_dump(mode="json")
            view.limitations = list(report.limitations)
        elif registration["stop_reason"] == "reconciliation_required":
            view.limitations = [
                ("A prior process stopped while this loop was running. Action "
                 "outcome and consumed budget are unknown; the request was not "
                 "replayed and requires reconciliation.")
            ]
        else:
            view.limitations = [
                ("The loop report artifact is not available; only registry "
                "metadata is shown.")
            ]
        return view.model_dump(mode="json")

    def list_loops(
        self, *, base_request_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        registrations = self.registry.list(
            base_request_id=base_request_id, limit=limit, offset=offset,
        )
        items = []
        for registration in registrations:
            try:
                items.append(self.get_view(registration["loop_id"]))
            except LoopServiceError:
                continue
        return InvestigationLoopListResponse(
            items=[InvestigationLoopView.model_validate(item) for item in items],
            count=len(items), offset=offset, limit=limit,
            next_offset=offset + len(items) if len(items) == limit else None,
        ).model_dump(mode="json")

    def decisions(self, loop_id: str) -> dict[str, Any]:
        registration = self.registry.get(loop_id)
        if registration is None:
            raise LoopServiceError("LOOP_NOT_FOUND")
        report = self._load_report(registration["report_artifact_id"])
        if report is None:
            return LoopDecisionsResponse(loop_id=loop_id, items=[], count=0).model_dump(mode="json")
        items = [self._decision_view(loop_id, decision) for decision in report.decisions]
        return LoopDecisionsResponse(
            loop_id=loop_id, items=items, count=len(items),
        ).model_dump(mode="json")

    def evidence_changes(self, loop_id: str) -> dict[str, Any]:
        registration = self.registry.get(loop_id)
        if registration is None:
            raise LoopServiceError("LOOP_NOT_FOUND")
        report = self._load_report(registration["report_artifact_id"])
        if report is None:
            return LoopEvidenceChangesResponse(
                loop_id=loop_id, items=[], count=0,
                limitations=["loop report artifact unavailable"],
            ).model_dump(mode="json")
        items = [
            self._decision_view(loop_id, decision)
            for decision in report.decisions if decision.new_evidence
        ]
        return LoopEvidenceChangesResponse(
            loop_id=loop_id, items=items, count=len(items),
            limitations=[
                ("Rounds without newly discovered evidence are omitted; the "
                "authoritative per-round record is the decisions endpoint.")
            ],
        ).model_dump(mode="json")

    def budget(self, loop_id: str) -> dict[str, Any]:
        registration = self.registry.get(loop_id)
        if registration is None:
            raise LoopServiceError("LOOP_NOT_FOUND")
        report = self._load_report(registration["report_artifact_id"])
        if report is None:
            return LoopBudgetResponse(
                loop_id=loop_id,
                remaining={"actions": None, "model_calls": None, "seconds": None},
            ).model_dump(mode="json")
        remaining: dict[str, float | int | None] = {
            "actions": max(
                0, report.budget.max_actions - report.metrics.rounds_executed,
            ),
            "model_calls": max(
                0, report.budget.max_model_calls - report.metrics.model_calls_used,
            ),
            "seconds": None,  # wall-clock budget is only meaningful during a run
        }
        return LoopBudgetResponse(
            loop_id=loop_id,
            budget=report.budget.model_dump(mode="json"),
            metrics=report.metrics.model_dump(mode="json"),
            remaining=remaining,
        ).model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _load_report(self, artifact_id: str) -> InvestigationLoopReport | None:
        if not artifact_id or artifact_id in {"pending", "failed"}:
            return None
        try:
            payload = json.loads(
                self.artifacts.get_bytes(artifact_id).decode("utf-8"),
            )
        except (ArtifactIntegrityError, ArtifactNotFoundError, ValueError, UnicodeDecodeError, OSError):
            return None
        try:
            return InvestigationLoopReport.model_validate(payload)
        except ValueError:
            return None

    def _recoverable_report(
        self, registration: dict[str, Any],
    ) -> tuple[InvestigationLoopReport | None, str | None, bool]:
        artifact_id = registration["report_artifact_id"]
        if artifact_id not in {"", "pending", "failed"}:
            report = self._load_report(artifact_id)
            try:
                metadata = self.artifacts.get_metadata(artifact_id)
            except (ArtifactIntegrityError, ArtifactNotFoundError, ValueError):
                metadata = None
            if (
                report is not None
                and metadata is not None
                and "investigation-loop" in metadata.tags
                and metadata.metadata.get("registration_id") == registration["loop_id"]
                and metadata.metadata.get("base_request_id") == registration["base_request_id"]
                and metadata.metadata.get("strategy") == registration["strategy"]
                and report.registration_id == registration["loop_id"]
                and report.base_request_id == registration["base_request_id"]
                and report.strategy == registration["strategy"]
            ):
                report.report_artifact_id = artifact_id
                return report, artifact_id, False
            return None, None, False

        candidates = [
            item.artifact_ref.artifact_id
            for item in self.artifacts.list_metadata()
            if "investigation-loop" in item.tags
            and item.metadata.get("registration_id") == registration["loop_id"]
            and item.metadata.get("base_request_id") == registration["base_request_id"]
            and item.metadata.get("strategy") == registration["strategy"]
        ]
        if len(candidates) != 1:
            return None, None, len(candidates) > 1
        artifact_id = candidates[0]
        report = self._load_report(artifact_id)
        if (
            report is None
            or report.registration_id != registration["loop_id"]
            or report.base_request_id != registration["base_request_id"]
            or report.strategy != registration["strategy"]
        ):
            return None, None, False
        report.report_artifact_id = artifact_id
        return report, artifact_id, False

    @staticmethod
    def _decision_view(loop_id: str, decision) -> LoopDecisionView:
        evidence_refs = list(decision.evidence_refs)
        after = evidence_refs
        before: list[str] | None = None  # older artifacts did not record it
        return LoopDecisionView(
            decision_id=f"{loop_id}:round-{decision.round_index}",
            round_index=decision.round_index,
            phase=decision.phase,
            strategy=decision.selection_strategy,
            candidate_actions=list(decision.candidate_actions),
            selected_action_id=decision.selected_action,
            selection_reason=decision.selection_reason,
            evidence_refs=evidence_refs,
            evidence_refs_before=before,
            evidence_refs_after=after,
            new_evidence_count=len(evidence_refs) if decision.new_evidence else 0,
            cost_estimate=decision.cost_estimate,
            actual_cost=decision.actual_cost,
            remaining_budget={},
            result_status=decision.result_status,
            new_evidence=decision.new_evidence,
            stop_reason=decision.stop_reason,
        )
