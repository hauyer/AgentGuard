"""复用内容寻址工件库，保存不可变安全配置、标注与决策。"""
import json
from threading import RLock

from agentguard.services.graded_security import fingerprint

_WRITE_LOCK = RLock()


class GradedRegistry:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def save(self, kind, study_id, payload):
        raw = json.dumps({"kind": kind, "study_id": study_id, "payload": payload}, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        with _WRITE_LOCK:
            stored = self.artifacts.put(raw, name=f"gsei-{kind}.json", media_type="application/json",
                tags=["gsei", kind], metadata={"study_id": study_id})
        return {"artifact_id": stored.artifact_ref.artifact_id, "payload": payload, "payload_sha256": fingerprint(payload)}

    def read(self, kind, study_id, artifact_id):
        value = json.loads(self.artifacts.get_bytes(artifact_id))
        if value["kind"] != kind or value["study_id"] != study_id:
            raise ValueError("GSEI_ARTIFACT_SCOPE_MISMATCH")
        return value["payload"]

    def list_profiles(self, study_id):
        return [{"artifact_id": r.artifact_ref.artifact_id,
                 "profile": self.read("profile", study_id, r.artifact_ref.artifact_id)}
                for r in self.artifacts.list_metadata() if "gsei" in r.tags and "profile" in r.tags and r.metadata.get("study_id") == study_id]
