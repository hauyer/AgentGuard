import hashlib
import pytest
from agentguard.artifacts.local_store import (
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    LocalArtifactStore,
)


def test_artifact_round_trip_and_content_addressing(tmp_path):
    store = LocalArtifactStore(tmp_path)
    data = b"trace bytes"
    record = store.put(
        data,
        name="trace",
        description="test trace",
        tags=["test"],
        metadata={"run_id": "run-1"},
        media_type="application/octet-stream",
    )
    assert record.artifact_ref.sha256 == hashlib.sha256(data).hexdigest()
    assert store.get_bytes(record.artifact_ref) == data
    assert store.get_metadata(record.artifact_ref.artifact_id).name == "trace"
    assert len(store.list_metadata()) == 1


def test_artifact_missing_and_bad_digest(tmp_path):
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFoundError):
        store.get_bytes("0" * 64)
    with pytest.raises(ValueError):
        store.get_bytes("../not-a-digest")


def test_artifact_read_detects_content_corruption(tmp_path):
    store = LocalArtifactStore(tmp_path)
    record = store.put(b"immutable trace")
    blob_path, _ = store._paths(record.artifact_ref.sha256)
    blob_path.write_bytes(b"tampered trace")
    with pytest.raises(ArtifactIntegrityError):
        store.get_bytes(record.artifact_ref.artifact_id)
