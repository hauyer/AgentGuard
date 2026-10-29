"""Connect immutable Studies to the existing WARDEN lifecycle and artifact verifier."""
from __future__ import annotations

import hashlib
import json

from agentguard.adapters.warden import WardenKernelError
from agentguard.contracts.experiment_view import EventArtifact, LiveResearchManifest
from agentguard.contracts.research_result import WardenResearchManifest
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.services.warden_lifecycle import WardenLifecycleService


class StudyExecutionService:
    def __init__(self, registry, workflows, client, artifacts):
        self.registry, self.workflows = registry, workflows
        self.client, self.artifacts = client, artifacts

    def submit_batch(self, study_id: str, key: str) -> dict:
        """Freeze all registered arms in an immutable manifest; reuse the durable queue.

        The manifest is written before submission. Replaying the batch resolves the same
        per-arm keys, including an interrupted partial submission, without model threads.
        """
        study = self.registry.get(study_id)
        if study is None:
            raise KeyError(study_id)
        if not self.registry.verify_registration(study_id):
            raise ValueError("STUDY_REGISTRATION_CORRUPT")
        batch_id = "batch-" + hashlib.sha256(f"{study_id}:{key}".encode()).hexdigest()
        arms = []
        for arm in study.arms:
            arm_key = batch_id + ":" + arm.arm_id
            # submit() scopes a client key to the immutable Study.
            workflow = "study-" + hashlib.sha256(f"{study_id}:{arm_key}".encode()).hexdigest()
            arms.append({"arm_id": arm.arm_id, "workflow_id": workflow,
                         "protocol_ref": study.protocol_ref})
        manifest = {"schema_version": "stego.batch/1", "batch_id": batch_id,
                    "study_id": study_id, "protocol_ref": study.protocol_ref,
                    "split_sha256": study.split_sha256, "seed": study.seed, "arms": arms}
        saved = self.artifacts.put(json.dumps(manifest, sort_keys=True).encode(),
            name=batch_id + ".json", media_type="application/json",
            description="Immutable preregistered multi-arm submission manifest",
            tags=["research", "batch"], metadata={"study_id": study_id, "batch_id": batch_id})
        items = []
        for arm in arms:
            try:
                submission = self.submit(study_id, arm["arm_id"], batch_id + ":" + arm["arm_id"])
            except WardenKernelError as exc:
                # Retain partial progress. No ambiguous transport failure is retried here.
                submission = {"status": "submission_error", "error": exc.code}
            items.append({**arm, "submission": submission})
        return {**manifest, "manifest_artifact_id": saved.artifact_ref.artifact_id, "items": items}

    def list_batches(self, study_id: str, *, limit=50, offset=0) -> dict:
        """Reconcile a lost HTTP response through verified immutable manifests; no writes."""
        study = self.registry.get(study_id)
        if study is None:
            raise KeyError(study_id)
        manifests = []
        for record in self.artifacts.list_metadata():
            if "batch" not in record.tags or record.metadata.get("study_id") != study_id:
                continue
            raw = self.artifacts.get_bytes(record.artifact_ref.artifact_id)
            manifest = json.loads(raw)
            if (manifest.get("schema_version") != "stego.batch/1" or manifest.get("study_id") != study_id
                    or manifest.get("protocol_ref") != study.protocol_ref):
                raise ValueError("BATCH_MANIFEST_IDENTITY_MISMATCH")
            items = []
            for arm in manifest["arms"]:
                entry = self.workflows.get(arm["workflow_id"])
                if entry is not None and entry.get("request", {}).get("study_id") != study_id:
                    raise ValueError("BATCH_WORKFLOW_IDENTITY_MISMATCH")
                items.append({**arm, "run_id": entry.get("run_id") if entry else None,
                              "submission_status": entry["workflow_status"] if entry else "not_submitted"})
            manifests.append({**manifest, "manifest_artifact_id": record.artifact_ref.artifact_id, "items": items})
        manifests.sort(key=lambda item: item["batch_id"])
        return {"schema_version": "stego.batches/1", "items": manifests[offset:offset + limit],
                "total": len(manifests), "limit": limit, "offset": offset}

    def submit(self, study_id: str, arm_id: str, key: str) -> dict:
        study = self.registry.get(study_id)
        if study is None:
            raise KeyError(study_id)
        if not self.registry.verify_registration(study_id):
            raise ValueError("STUDY_REGISTRATION_CORRUPT")
        arm = next((item for item in study.arms if item.arm_id == arm_id), None)
        if arm is None:
            raise ValueError("STUDY_ARM_NOT_REGISTERED")
        if not all((study.protocol_ref, study.warden_contract_id, study.warden_runtime_id,
                    study.warden_mode, study.model_id, study.warden_capture_targets)):
            return {"status": "blocked", "blockers": ["STUDY_EXECUTION_METADATA_MISSING"]}
        # Scope the caller key to the Study, so changing the arm conflicts in the
        # existing durable workflow journal instead of silently starting a second run.
        request_id = "study-" + hashlib.sha256(f"{study_id}:{key}".encode()).hexdigest()
        request = WardenAuditRequest(
            request_id=request_id, study_id=study_id, study_arm_id=arm_id,
            research_protocol_ref=study.protocol_ref, study_arm_ref=arm_id,
            contract_id=study.warden_contract_id, runtime_id=study.warden_runtime_id,
            model_id=study.model_id, mode=study.warden_mode,
            capture_targets=study.warden_capture_targets, probe_id=study.warden_probe_id,
            sae_id=study.warden_sae_id, metadata={"study_id": study_id},
        )
        result = WardenLifecycleService(self.client, workflows=self.workflows).execute(request)
        return {**result, "workflow_id": request_id, "study_id": study_id, "arm_id": arm_id}

    def result(self, study_id: str, workflow_id: str) -> dict:
        entry = self.workflows.get(workflow_id)
        if entry is None or entry.get("request", {}).get("study_id") != study_id:
            raise KeyError(workflow_id)
        result = WardenLifecycleService(
            self.client, workflows=self.workflows,
        ).reconcile_existing(workflow_id)
        run_id = result.get("run_id") or entry.get("run_id")
        if not run_id:
            return {**result, "source_validation": "not_run"}
        run = self.client.get_run(run_id)
        study = self.registry.get(study_id)
        if run.get("status") != "completed" and not (
            run.get("status") == "failed" and study.track == "live_steganography"
        ):
            return {**result, "run": run, "source_validation": "not_verified"}
        manifests = []
        for ref in (run.get("evidence") or {}).get("artifacts", []):
            if ref.get("kind") != "public-summary":
                continue
            raw = self.client.read_public_artifact(ref["artifact_id"], ref["content_hash"])
            manifest = json.loads(raw)
            if manifest.get("schema_version") == "stego.events/1":
                continue
            is_live = manifest.get("schema_version") == "warden.research-run/2"
            if is_live:
                LiveResearchManifest.model_validate(manifest)
            else:
                WardenResearchManifest.model_validate(manifest)
            expected = {
                "schema_version": "warden.research-run/2" if is_live else "warden.research-run/1",
                "run_id": run_id,
                "contract_id": study.warden_contract_id,
                "plan_id": run["plan_id"], "study_id": study_id,
                "arm_id": entry["request"]["study_arm_id"],
                "protocol_ref": study.protocol_ref, "model_id": study.model_id,
                "model_revision": study.model_revision,
                "tokenizer_revision": study.tokenizer_revision, "split_sha256": study.split_sha256,
                "status": run["status"] if is_live else "completed",
                "provenance": "real_sender_receiver" if is_live else "local_real_model",
                "seed": study.seed,
            }
            if any(manifest.get(key) != value for key, value in expected.items()):
                raise ValueError("WARDEN_MANIFEST_STUDY_MISMATCH")
            if manifest.get("track") != study.track:
                raise ValueError("WARDEN_MANIFEST_TRACK_MISMATCH")
            if (len(study.dataset_version or "") == 64
                    and manifest.get("dataset_sha256") != study.dataset_version):
                raise ValueError("WARDEN_MANIFEST_DATASET_MISMATCH")
            stored = self.artifacts.put(
                raw, name=f"{run_id}-verified-summary.json", media_type="application/json",
                description="Hash-checked WARDEN public research summary",
                tags=["research", "local_real_model"],
                metadata={"study_id": study_id, "run_id": run_id,
                          "remote_artifact_id": ref["artifact_id"], "sha256": ref["content_hash"]},
            )
            manifests.append({"artifact_id": ref["artifact_id"], "sha256": ref["content_hash"],
                              "local_artifact_id": stored.artifact_ref.artifact_id,
                              "manifest": manifest})
        return {**result, "run": run, "artifacts": manifests,
                "source_validation": "hash_and_study_verified" if manifests else "not_verified",
                "evidence_mode": (manifests[0]["manifest"]["provenance"] if manifests else "unknown"),
                "analysis_status": "incomplete",
                "analysis_blockers": ["NO_VERIFIED_PAIRED_OUTCOMES"],
                "note": "Activation capture does not establish communication or defense efficacy."}

    def events(self, study_id: str, workflow_id: str) -> tuple[dict, list[dict]]:
        """Import only the event artifact bound by the verified S manifest and Run evidence."""
        result = self.result(study_id, workflow_id)
        manifests = result.get("artifacts", [])
        live = next((item["manifest"] for item in manifests
                     if item["manifest"].get("schema_version") == "warden.research-run/2"), None)
        if live is None:
            return result, []
        ref = live["events_artifact"]
        known = {item["artifact_id"]: item for item in result["run"]["evidence"]["artifacts"]}
        if (ref["artifact_id"] not in known
                or known[ref["artifact_id"]]["content_hash"] != ref["content_hash"]):
            raise ValueError("EVENT_ARTIFACT_NOT_BOUND_TO_RUN")
        raw = self.client.read_public_artifact(ref["artifact_id"], ref["content_hash"])
        event_artifact = EventArtifact.model_validate_json(raw)
        if (event_artifact.study_id != study_id or event_artifact.run_id != live["run_id"]
                or event_artifact.arm_id != live["arm_id"]):
            raise ValueError("EVENT_ARTIFACT_IDENTITY_MISMATCH")
        return result, [event.model_dump(mode="json") for event in event_artifact.events]
