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


def reference_is_safe(ref: str) -> bool:
    """Shared artifact-reference safety rule for upper-layer projections."""
    return EvidenceGateway._safe_reference(ref)
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
            refs, verified_refs, dropped, failed = self._validate_refs(
                [*item.artifact_refs, *([report_artifact_id] if report_artifact_id else [])],
                artifacts=artifacts,
                expected_run_id=report.run_id,
            )
            source_limitations = item.metadata.get("limitations")
            limitations = list(dict.fromkeys([
                *(source_limitations if isinstance(source_limitations, list) else []),
                *(["One or more unsafe, mismatched, or locally invalid artifact references were omitted unopened."] if dropped else []),
                *(["Only AgentGuard's local content-addressed artifacts were hash-checked; external references were not fetched."] if len(verified_refs) != len(refs) else []),
                *(["Synthetic evidence is not a real model measurement or causal conclusion."] if item.grade == "synthetic" or report.backend_mode == "synthetic" else []),
                *(["A trusted, traceable methodology-review record is unavailable; upstream booleans are not review evidence."]),
            ]))
            level = item.grade
            criteria = None
            if level == "causal_validated":
                level = "interventional" if item.kind == "intervention" else "observational"
                limitations.append(
                    "A claimed causal grade was withheld: local content hashes do not prove source authenticity, "
                    "independent runs, matched/negative controls, or a traceable independent review."
                )
            if report.backend_mode == "synthetic" or item.grade == "synthetic":
                level = "synthetic"
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
                verification_status="unverified",
                content_integrity_status=(
                    "failed" if failed else
                    "verified" if refs and len(verified_refs) == len(refs) else "unverified"
                ),
                source_authenticity_status="unknown",
                methodology_review_status="unknown",
                run_status=report.status,
                model_version="unknown",
                scenario_version=str(item.metadata.get("scenario_version", "unknown")),
                created_at=item.created_at,
                causal_validation=criteria if level == "causal_validated" else None,
                content_access_supported=bool(verified_refs),
                integrity_verified=bool(refs) and len(verified_refs) == len(refs),
                access_restriction="local_verified_read" if verified_refs else "read_only_reference",
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
        metadata = request.get("metadata") if isinstance(request.get("metadata"), Mapping) else {}
        source_kind = metadata.get("source_kind")
        if source_kind in {"synthetic", "stub"}:
            return {
                "request_id": entry.get("request_id"),
                "workflow_status": entry.get("workflow_status", "unknown"),
                "run_status": run_status,
                "items": [],
                "limitations": [
                    (f"Workflow declares source_kind={source_kind}; synthetic or stub data "
                     "was not emitted as WARDEN-produced evidence.")
                ],
            }
        notes: list[str] = []
        identity_valid = bool(
            isinstance(run, Mapping)
            and isinstance(expected_run_id, str)
            and actual_run_id == expected_run_id
            and isinstance(plan_id, str)
            and bool(plan_id.strip())
            and run.get("plan_id") == plan_id
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
        refs, verified_refs, dropped, failed = self._validate_refs(
            raw_refs, artifacts=artifacts, expected_run_id=str(expected_run_id),
            expected_plan_id=str(plan_id),
        )
        if dropped:
            notes.append("Unsafe local paths or mismatched artifact references were omitted; no path was opened.")
        if not refs:
            notes.append("WARDEN returned no usable evidence or artifact references.")
        if len(verified_refs) != len(refs):
            notes.append(
                "WARDEN artifact content and run/plan association could not be independently verified; "
                "external references were not fetched."
            )
        if failed:
            notes.append("At least one local WARDEN artifact failed integrity or ownership validation.")
        notes.append(
            "Upstream WARDEN v1 provides no verifiable artifact-source signature or "
            "traceable methodology-review contract."
        )

        raw_items = self._warden_evidence_items(run)
        if not raw_items:
            raw_items = [{
                "type": "run_summary",
                "summary": "WARDEN reported a completed audit run.",
                "artifact_refs": raw_refs,
                "run_id": expected_run_id,
                "plan_id": plan_id,
            }]
        output: list[GovernedEvidence] = []
        for index, raw in enumerate(raw_items):
            item_run_id = raw.get("run_id")
            item_plan_id = raw.get("plan_id")
            if (
                (item_run_id is not None and item_run_id != expected_run_id)
                or (item_plan_id is not None and item_plan_id != plan_id)
            ):
                notes.append("A WARDEN evidence item named a different run or plan and was omitted.")
                continue
            if raw.get("synthetic") is True or raw.get("source_kind") in {"synthetic", "stub"}:
                notes.append("A synthetic or stub evidence item was omitted from the real WARDEN evidence projection.")
                continue
            item_refs, item_verified, item_dropped, item_failed = self._validate_refs(
                self._refs_from_item(raw),
                artifacts=artifacts,
                expected_run_id=str(expected_run_id),
                expected_plan_id=str(plan_id),
            )
            limitations = self._bounded_strings(raw.get("limitations"))
            if not item_refs:
                limitations.append("No safe artifact reference was available for this evidence item.")
            if item_dropped:
                limitations.append("Unsafe local paths or mismatched artifact references were omitted; no path was opened.")
            if len(item_verified) != len(item_refs):
                limitations.append(
                    "Content integrity or the required run/plan association was not independently checked; "
                    "external references were not fetched."
                )
            limitations.append(
                "WARDEN v1 has no verifiable artifact-source signature; source authenticity remains unknown."
            )
            if raw.get("methodology_reviewed") is True:
                limitations.append(
                    "upstream WARDEN supplied methodology_reviewed=true without a traceable review record; "
                    "it remains unverified."
                )
            else:
                limitations.append("No traceable independent methodology-review record was available.")
            evidence_type = self._evidence_type(raw.get("evidence_type", raw.get("type")))
            level = self._warden_level(raw, request)
            criteria = None
            if raw.get("evidence_level", raw.get("evidence_grade")) == "causal_validated":
                level = "interventional" if evidence_type == "intervention" else "observational"
                limitations.append(
                    "upstream WARDEN's causal claim was withheld because reported booleans and hashes do not prove "
                    "independent runs, matched/negative controls, utility, source authenticity, or independent review."
                )
            content_status = (
                "verified" if item_refs and len(item_verified) == len(item_refs) else "unverified"
            )
            if item_failed:
                content_status = "failed"
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
                verification_status="unverified",
                content_integrity_status=content_status,
                source_authenticity_status="unknown",
                methodology_review_status=(
                    "unverified" if raw.get("methodology_reviewed") is True else "unknown"
                ),
                run_status="completed",
                model_version=str(request.get("model_id") or "unknown"),
                scenario_version=str((request.get("metadata") or {}).get("scenario_version", "unknown")) if isinstance(request.get("metadata"), Mapping) else "unknown",
                created_at=self._timestamp(raw.get("created_at") or run.get("completed_at") or run.get("updated_at")),
                causal_validation=criteria if level == "causal_validated" else None,
                content_access_supported=bool(item_verified),
                integrity_verified=bool(item_refs) and len(item_verified) == len(item_refs),
                access_restriction="local_verified_read" if item_verified else "read_only_reference",
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
        expected_plan_id: str | None = None,
    ) -> tuple[list[str], list[str], bool, bool]:
        safe_refs: list[str] = []
        verified_refs: list[str] = []
        dropped = False
        failed = False
        for raw in dict.fromkeys(refs):
            if not isinstance(raw, str):
                dropped = True
                continue
            ref = raw.strip()
            if not self._safe_reference(ref):
                dropped = True
                continue
            digest = self._artifact_digest(ref)
            if digest is None:
                safe_refs.append(ref)
                continue
            try:
                metadata = artifacts.get_metadata(digest)
                content = artifacts.get_bytes(digest)
            except ArtifactIntegrityError:
                failed = True
                dropped = True
                continue
            except (ArtifactNotFoundError, ValueError):
                safe_refs.append(ref)
                continue
            if hashlib.sha256(content).hexdigest() != digest:
                failed = True
                dropped = True
                continue
            ref_record = metadata.artifact_ref
            if (
                ref_record.artifact_id != digest
                or ref_record.sha256 != digest
                or ref_record.uri != f"artifact://sha256/{digest}"
                or ref_record.size_bytes != len(content)
            ):
                failed = True
                dropped = True
                continue
            artifact_run_id = metadata.metadata.get("run_id")
            artifact_plan_id = metadata.metadata.get("plan_id")
            if artifact_run_id != expected_run_id:
                failed = True
                dropped = True
                continue
            if expected_plan_id is not None and artifact_plan_id != expected_plan_id:
                failed = True
                dropped = True
                continue
            try:
                payload = json.loads(content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = None
            if not isinstance(payload, Mapping):
                dropped = True
                continue
            if payload.get("run_id") != expected_run_id:
                failed = True
                dropped = True
                continue
            if expected_plan_id is not None and payload.get("plan_id") != expected_plan_id:
                failed = True
                dropped = True
                continue
            manifest = payload.get("manifest")
            if isinstance(manifest, Mapping) and (
                manifest.get("run_id") != expected_run_id
                or (expected_plan_id is not None and manifest.get("plan_id") != expected_plan_id)
                or manifest.get("content_sha256") != digest
            ):
                failed = True
                dropped = True
                continue
            safe_refs.append(ref)
            verified_refs.append(ref)
        return safe_refs, verified_refs, dropped, failed

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
        if normalized == "completed":
            return "completed"
        if normalized in {"partial", "incomplete", "timed_out"}:
            return "partial"
        if normalized in {"blocked", "unavailable", "unsupported"}:
            return "blocked"
        if normalized in {"failed", "cancelled"}:
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
        if (
            item.get("synthetic") is True
            or item.get("source_kind") in {"synthetic", "stub"}
            or metadata.get("evidence_level") == "synthetic"
        ):
            return "synthetic"
        claimed = item.get("evidence_level", item.get("evidence_grade"))
        if claimed == "causal_validated":
            return (
                "interventional"
                if item.get("evidence_type", item.get("type")) == "intervention"
                and item.get("intervention_status") in {"completed", "measured"}
                else "observational"
            )
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

