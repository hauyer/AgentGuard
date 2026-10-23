"""Content-addressed local artifact storage with hash verification."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from agentguard.contracts.artifacts import ArtifactMetadata, ArtifactRef


class ArtifactNotFoundError(FileNotFoundError):
    pass


class ArtifactIntegrityError(RuntimeError):
    pass


class LocalArtifactStore:
    """Store immutable bytes by SHA-256; metadata is written beside each artifact."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, digest: str) -> tuple[Path, Path]:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("artifact digest must be a lowercase SHA-256 hex string")
        directory = self.root / digest[:2]
        return directory / digest[2:], directory / f"{digest[2:]}.json"

    def put(
        self,
        data: bytes,
        *,
        name: str = "",
        description: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, str | int | float | bool] | None = None,
        media_type: str = "application/octet-stream",
    ) -> ArtifactMetadata:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        blob_path, meta_path = self._paths(digest)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():
            tmp_path = blob_path.with_suffix(".tmp")
            tmp_path.write_bytes(data)
            os.replace(tmp_path, blob_path)

        ref = ArtifactRef(
            artifact_id=digest,
            uri=f"artifact://sha256/{digest}",
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
        )
        record = ArtifactMetadata(
            artifact_ref=ref,
            name=name,
            description=description,
            tags=tags or [],
            metadata=metadata or {},
        )
        # Keep the first content-addressed blob immutable; metadata is a manifest.
        if not meta_path.exists():
            tmp_meta = meta_path.with_suffix(".tmp")
            tmp_meta.write_text(
                record.model_dump_json(indent=2), encoding="utf-8"
            )
            os.replace(tmp_meta, meta_path)
        return self.get_metadata(digest)

    def get_metadata(self, artifact_id: str | ArtifactRef) -> ArtifactMetadata:
        digest = artifact_id.artifact_id if isinstance(artifact_id, ArtifactRef) else artifact_id
        _, meta_path = self._paths(digest)
        if not meta_path.exists():
            raise ArtifactNotFoundError(f"Artifact metadata not found: {digest}")
        return ArtifactMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def get_bytes(self, artifact_id: str | ArtifactRef) -> bytes:
        digest = artifact_id.artifact_id if isinstance(artifact_id, ArtifactRef) else artifact_id
        blob_path, _ = self._paths(digest)
        if not blob_path.exists():
            raise ArtifactNotFoundError(f"Artifact data not found: {digest}")
        data = blob_path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise ArtifactIntegrityError(
                f"Artifact hash mismatch: expected {digest}, got {actual}"
            )
        return data

    def list_metadata(self) -> list[ArtifactMetadata]:
        records: list[ArtifactMetadata] = []
        for path in sorted(self.root.glob("*/*.json")):
            try:
                records.append(ArtifactMetadata.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                # Corrupt manifests are skipped by listing; direct lookup still surfaces errors.
                continue
        return records
