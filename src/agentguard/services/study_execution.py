"""Connect immutable Studies to the existing WARDEN lifecycle and artifact verifier."""
from __future__ import annotations

import hashlib
import json

from agentguard.contracts.research_result import WardenResearchManifest
from agentguard.contracts.warden import WardenAuditRequest
from agentguard.services.warden_lifecycle import WardenLifecycleService


class StudyExecutionService:
    def __init__(self, registry, workflows, client, artifacts):
        self.registry, self.workflows = registry, workflows
        self.client, self.artifacts = client, artifacts

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
        if run.get("status") != "completed":
            return {**result, "run": run, "source_validation": "not_verified"}
        study = self.registry.get(study_id)
        manifests = []
        for ref in (run.get("evidence") or {}).get("artifacts", []):
            if ref.get("kind") != "public-summary":
                continue
            raw = self.client.read_public_artifact(ref["artifact_id"], ref["content_hash"])
            manifest = json.loads(raw)
            WardenResearchManifest.model_validate(manifest)
            expected = {
                "schema_version": "warden.research-run/1", "run_id": run_id,
                "contract_id": study.warden_contract_id,
                "plan_id": run["plan_id"], "study_id": study_id,
                "arm_id": entry["request"]["study_arm_id"],
                "protocol_ref": study.protocol_ref, "model_id": study.model_id,
                "model_revision": study.model_revision,
                "tokenizer_revision": study.tokenizer_revision, "split_sha256": study.split_sha256,
                "status": "completed", "provenance": "local_real_model",
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
                "evidence_mode": "local_real_model" if manifests else "unknown",
                "analysis_status": "incomplete",
                "analysis_blockers": ["NO_VERIFIED_PAIRED_OUTCOMES"],
                "note": "Activation capture does not establish communication or defense efficacy."}
