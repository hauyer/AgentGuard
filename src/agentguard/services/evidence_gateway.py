"""Evidence Gateway for provenance-aware AgentGuard and WARDEN summaries.

The gateway treats WARDEN artifact URIs as references only. It never follows remote
links or reads paths from a kernel response. Local artifacts are read only through
AgentGuard's content-addressed store and are checked against their SHA-256 identity.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from agentguard.artifacts.local_store import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    LocalArtifactStore,
)
from agentguard.contracts.evidence import (
    CausalValidationCriteria,
    GovernedEvidence,
)
from agentguard.contracts.report import RunReport
from agentguard.services.warden_lifecycle import WardenLifecycleService

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_URI = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_WINDOWS_ABSOLUTE = re.compile(r"^[a-zA-Z]:[\\/]")
_TYPES = {
    "observation", "finding", "hypothesis", "experiment_design", "intervention",
    "defense", "evaluation", "run_summary", "note",
}


class EvidenceGateway:
    """Convert internal records into evidence summaries with conservative claims."""

    def for_report(
        self,
        report: RunReport,
        *,
        report_artifact_id: str | None,
        artifacts: LocalArtifactStore,
    ) -> list[GovernedEvidence]:
        output: list[GovernedEvidence] = []
        for item in report.evidence:
            refs, verified_refs, dropped = self._validate_refs(
                [*item.artifact_refs, *([report_artifact_id] if report_artifact_id else [])],
                artifacts=artifacts,
                expected_run_id=report.run_id,
            )
            source_limitations = item.metadata.get("limitations")
            limitations = list(dict.fromkeys([
                *(source_limitations if isinstance(source_limitations, list) else []),
                *(["One or more local file or unsafe artifact references were omitted; no referenced path was opened."] if dropped else []),
                *(["Only AgentGuard's local content-addressed artifacts were hash-checked; external references were not fetched."] if len(verified_refs) != len(refs) else []),
                *(["Synthetic evidence is not a real model measurement or causal conclusion."] if item.grade == "synthetic" or report.backend_mode == "synthetic" else []),
                *(["Run evidence has no independent methodological review; successful run status alone does not validate its claim."] if not item.metadata.get("methodology_reviewed", False) else []),
            ]))
            level = item.grade
            criteria = self._causal_criteria(item.metadata.get("causal_validation"))
            status = "verified" if (
                report.status == "completed"
                and report.backend_mode == "real"
                and bool(refs)
                and len(verified_refs) == len(refs)
                and item.metadata.get("methodology_reviewed") is True
            ) else "unverified"
            if level == "causal_validated" and (criteria is None or status != "verified"):
                level = "interventional" if item.kind == "intervention" else "observational"
                limitations.append("A claimed causal grade was withheld because provenance and explicit methodological checks were incomplete.")
                criteria = None
            confidence = item.metadata.get("calibrated_confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not isinstance(item.metadata.get("confidence_calibration"), str):
                confidence = None
            output.append(GovernedEvidence(
                evidence_id=item.evidence_id,
                source_system=self._source_system(item.source),
                artifact_refs=refs,
                evidence_type=item.kind,
                evidence_level=level,
                scenario_id=report.scenario_id,
                risk_type=str(item.metadata.get("risk_type", "unknown")),
                confidence=confidence,
                limitations=limitations,
                verification_status=status,
                run_status=report.status,
                model_version="unknown",
                scenario_version=str(item.metadata.get("scenario_version", "unknown")),
                created_at=item.created_at,
                causal_validation=criteria if level == "causal_validated" else None,
            ))
        return output

    def for_warden_workflow(
        self,
        entry: Mapping[str, Any],
        *,
        artifacts: LocalArtifactStore,
    ) -> dict[str, Any]:
        """Summarize only the WARDEN run already recorded in the workflow journal."""
        context = entry.get("context") if isinstance(entry.get("context"), Mapping) else {}
        result = entry.get("result") if isinstance(entry.get("result"), Mapping) else {}
        request = entry.get("request") if isinstance(entry.get("request"), Mapping) else {}
        plan = context.get("plan") if isinstance(context.get("plan"), Mapping) else result.get("plan")
        run = context.get("run") if isinstance(context.get("run"), Mapping) else result.get("run")
        plan_id = plan.get("plan_id") if isinstance(plan, Mapping) else None
        expected_run_id = entry.get("run_id")
        actual_run_id = run.get("run_id") if isinstance(run, Mapping) else None
        raw_status = run.get("status") if isinstance(run, Mapping) else result.get("status")
        run_status = self._run_status(raw_status)
        notes: list[str] = []
        identity_valid = bool(
            isinstance(run, Mapping)
            and isinstance(expected_run_id, str)
            and actual_run_id == expected_run_id
            and (not isinstance(plan_id, str) or run.get("plan_id") == plan_id)
        )
        if not identity_valid:
            notes.append("The journal does not contain a matching WARDEN run and plan identity; evidence was not emitted.")
        if run_status != "completed":
            notes.append(f"WARDEN run status is {run_status}; no completed-run evidence was emitted.")
        if not identity_valid or run_status != "completed":
            return {
                "request_id": entry.get("request_id"),
                "workflow_status": entry.get("workflow_status", "unknown"),
                "run_status": run_status,
                "items": [],
                "limitations": notes,
            }

        raw_refs = WardenLifecycleService._evidence_refs(run)
        refs, verified_refs, dropped = self._validate_refs(
            raw_refs, artifacts=artifacts, expected_run_id=str(expected_run_id),
        )
        if dropped:
            notes.append("Unsafe local path or credential-bearing artifact references were omitted without opening them.")
        if not refs:
            notes.append("WARDEN returned no usable evidence or artifact references.")
        if len(verified_refs) != len(refs):
            notes.append("WARDEN artifact contents were not fetched; external and opaque references remain unverified.")

        raw_items = self._warden_evidence_items(run)
        if not raw_items:
            raw_items = [{"type": "run_summary", "summary": "WARDEN reported a completed audit run."}]
        output: list[GovernedEvidence] = []
        for index, raw in enumerate(raw_items):
            item_refs, item_verified, item_dropped = self._validate_refs(
                self._refs_from_item(raw) or raw_refs,
                artifacts=artifacts,
                expected_run_id=str(expected_run_id),
            )
            limitations = self._bounded_strings(raw.get("limitations"))
            if not item_refs:
                limitations.append("No safe artifact reference was available for this evidence item.")
            if item_dropped:
                limitations.append("Unsafe local path or credential-bearing artifact references were omitted; no path was opened.")
            if len(item_verified) != len(item_refs):
                limitations.append("Evidence payload integrity was not independently checked by AgentGuard.")
            evidence_type = self._evidence_type(raw.get("evidence_type", raw.get("type")))
            level = self._warden_level(raw, request)
            criteria = self._causal_criteria(raw.get("causal_validation"))
            provenance_verified = bool(
                item_refs
                and len(item_verified) == len(item_refs)
                and raw.get("methodology_reviewed") is True
            )
            verification_status = "verified" if provenance_verified else "unverified"
            if level == "causal_validated" and (
                evidence_type not in {"intervention", "evaluation"}
                or criteria is None
                or not provenance_verified
            ):
                level = "interventional" if evidence_type == "intervention" else "observational"
                criteria = None
                limitations.append("WARDEN causal grade was withheld because explicit experiment and provenance checks were incomplete.")
            confidence = raw.get("calibrated_confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not isinstance(raw.get("confidence_calibration"), str):
                confidence = None
            target_type, target_ref = self._safe_target(raw)
            output.append(GovernedEvidence(
                evidence_id=str(raw.get("evidence_id") or self._stable_evidence_id(str(expected_run_id), index)),
                source_system="WARDEN",
                warden_run_id=str(expected_run_id),
                warden_plan_id=str(plan_id) if plan_id else None,
                target_type=target_type,
                target_ref=target_ref,
                scenario_id=str(request.get("scenario_id") or "unknown"),
                artifact_refs=item_refs,
                evidence_type=evidence_type,
                evidence_level=level,
                risk_type=str(raw.get("risk_type") or request.get("risk_type") or "unknown"),
                confidence=confidence,
                limitations=limitations,
                verification_status=verification_status,
                run_status="completed",
                model_version=str(request.get("model_id") or "unknown"),
                scenario_version=str((request.get("metadata") or {}).get("scenario_version", "unknown")) if isinstance(request.get("metadata"), Mapping) else "unknown",
                created_at=self._timestamp(raw.get("created_at") or run.get("completed_at") or run.get("updated_at")),
                causal_validation=criteria if level == "causal_validated" else None,
            ))
        return {
            "request_id": entry.get("request_id"),
            "workflow_status": entry.get("workflow_status", "unknown"),
            "run_status": "completed",
            "items": [item.model_dump(mode="json") for item in output],
            "limitations": notes,
        }

    def verify_defense_artifacts(
        self,
        refs: list[str],
        *,
        run_id: str,
        candidate_id: str,
        artifacts: LocalArtifactStore,
        arrays: Mapping[str, Any],
    ) -> list[str]:
        """Return only artifacts whose content, ownership and measured arrays match."""
        verified: list[str] = []
        for ref in refs:
            digest = self._artifact_digest(ref)
            if digest is None:
                continue
            try:
                metadata = artifacts.get_metadata(digest)
                payload = json.loads(artifacts.get_bytes(digest).decode("utf-8"))
            except (ArtifactIntegrityError, ArtifactNotFoundError, ValueError, UnicodeDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            if metadata.metadata.get("run_id") != run_id or metadata.metadata.get("candidate_id") != candidate_id:
                continue
            if metadata.metadata.get("evidence_kind") != "paired_defense_verification":
                continue
            if payload.get("run_id") != run_id or payload.get("candidate_id") != candidate_id:
                continue
            if any(payload.get(key) != value for key, value in arrays.items()):
                continue
            requirements = (
                payload.get("matched_control_passed") is True,
                payload.get("negative_control_passed") is True,
                payload.get("benign_utility_measured") is True,
                payload.get("risk_transfer_checked") is True,
                payload.get("rollback_tested") is True,
                isinstance(payload.get("independent_repetitions"), int) and payload["independent_repetitions"] >= 2,
            )
            if all(requirements):
                verified.append(digest)
        return verified

    def _validate_refs(
        self,
        refs: list[str],
        *,
        artifacts: LocalArtifactStore,
        expected_run_id: str,
    ) -> tuple[list[str], list[str], bool]:
        safe_refs: list[str] = []
        verified_refs: list[str] = []
        dropped = False
        for raw in dict.fromkeys(refs):
            if not isinstance(raw, str):
                dropped = True
                continue
            ref = raw.strip()
            if not self._safe_reference(ref):
                dropped = True
                continue
            safe_refs.append(ref)
            digest = self._artifact_digest(ref)
            if digest is None:
                continue
            try:
                metadata = artifacts.get_metadata(digest)
                content = artifacts.get_bytes(digest)
            except (ArtifactIntegrityError, ArtifactNotFoundError, ValueError):
                continue
            if hashlib.sha256(content).hexdigest() != digest:
                continue
            artifact_run_id = metadata.metadata.get("run_id")
            if artifact_run_id == expected_run_id:
                verified_refs.append(ref)
        return safe_refs, verified_refs, dropped

    @staticmethod
    def _safe_reference(ref: str) -> bool:
        if not ref or len(ref) > 2048 or any(ord(char) < 32 for char in ref):
            return False
        try:
            parsed = urlsplit(ref)
        except ValueError:
            return False
        return not (
            parsed.scheme.lower() == "file"
            or ref.startswith(("\\\\", "//", "/"))
            or _WINDOWS_ABSOLUTE.match(ref)
            or (parsed.scheme.lower() in {"http", "https"} and (parsed.username or parsed.password))
            or (parsed.scheme.lower() in {"http", "https"} and (parsed.query or parsed.fragment))
        )

    @staticmethod
    def _artifact_digest(ref: str) -> str | None:
        match = _ARTIFACT_URI.fullmatch(ref)
        if match:
            return match.group(1)
        if _SHA256.fullmatch(ref):
            return ref
        return None

    @staticmethod
    def _source_system(source: str) -> str:
        prefix = source.split(".", 1)[0].strip()
        return prefix[:128] if prefix else "unknown"

    @staticmethod
    def _run_status(value: Any) -> str:
        normalized = str(value or "unknown").lower()
        if normalized in {"complete", "completed", "success", "succeeded"}:
            return "completed"
        if normalized in {"partial", "incomplete", "timed_out", "timeout"}:
            return "partial"
        if normalized in {"blocked", "unavailable", "unsupported"}:
            return "blocked"
        if normalized in {"failed", "error", "cancelled", "canceled"}:
            return "failed"
        return "unknown"

    @staticmethod
    def _evidence_type(value: Any) -> str:
        normalized = str(value or "").lower()
        aliases = {"observation": "observation", "run": "run_summary", "summary": "run_summary"}
        normalized = aliases.get(normalized, normalized)
        return normalized if normalized in _TYPES else "run_summary"

    @staticmethod
    def _warden_level(item: Mapping[str, Any], request: Mapping[str, Any]) -> str:
        metadata = request.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if item.get("synthetic") is True or metadata.get("evidence_level") == "synthetic":
            return "synthetic"
        claimed = item.get("evidence_level", item.get("evidence_grade"))
        if claimed == "causal_validated":
            return "causal_validated"
        if claimed == "interventional" and item.get("intervention_status") in {"completed", "measured"}:
            return "interventional"
        if item.get("evidence_type", item.get("type")) == "intervention" and item.get("intervention_status") in {"completed", "measured"}:
            return "interventional"
        return "observational"

    @staticmethod
    def _warden_evidence_items(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        for key in ("evidence_bundle", "evidence"):
            value = run.get(key)
            if isinstance(value, Mapping):
                for item_key in ("items", "evidence", "records"):
                    nested = value.get(item_key)
                    if isinstance(nested, list):
                        items.extend(item for item in nested if isinstance(item, Mapping))
            elif isinstance(value, list):
                items.extend(item for item in value if isinstance(item, Mapping))
        return items

    @staticmethod
    def _refs_from_item(item: Mapping[str, Any]) -> list[str]:
        refs: list[str] = []
        for key in ("artifact_refs", "evidence_refs"):
            value = item.get(key)
            if isinstance(value, list):
                refs.extend(ref for ref in value if isinstance(ref, str))
        artifacts = item.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, Mapping):
                    ref = artifact.get("artifact_id") or artifact.get("uri") or artifact.get("ref")
                    if isinstance(ref, str):
                        refs.append(ref)
        return list(dict.fromkeys(refs))

    @staticmethod
    def _safe_target(item: Mapping[str, Any]) -> tuple[str | None, str | None]:
        target_type = item.get("target_type")
        target_ref = item.get("target_ref")
        if target_type not in {"message", "tool_call", "tool_result", "activation"}:
            return None, None
        if (
            not isinstance(target_ref, str)
            or not target_ref.strip()
            or len(target_ref) > 256
            or target_ref != target_ref.strip()
            or any(char in target_ref for char in ("/", "\\", ":"))
            or any(ord(char) < 32 for char in target_ref)
        ):
            return None, None
        return target_type, target_ref

    @staticmethod
    def _causal_criteria(value: Any) -> CausalValidationCriteria | None:
        if not isinstance(value, Mapping):
            return None
        try:
            return CausalValidationCriteria.model_validate(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _stable_evidence_id(run_id: str, index: int) -> str:
        return "warden-ev-" + hashlib.sha256(f"{run_id}:{index}".encode()).hexdigest()[:20]

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                pass
        return datetime.now(UTC)

    @staticmethod
    def _bounded_strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item)[:1000] for item in value[:50] if isinstance(item, (str, int, float, bool))]

