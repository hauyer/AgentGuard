"""Read-only index over persisted AgentGuard run-report artifacts.

The index lists reports that actually exist in the content-addressed store
(tag ``run-report``). It duplicates no report storage and exposes no artifact
paths — only artifact ids, which resolve exclusively through the local store's
own digest-based lookup.
"""
from __future__ import annotations

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.artifacts import ArtifactMetadata


class ReportIndexService:
    """Paginated listing of persisted run reports with safe metadata only."""

    def __init__(self, artifacts: LocalArtifactStore) -> None:
        self.artifacts = artifacts

    def list_reports(
        self,
        *,
        status: str | None = None,
        scenario_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        records = [item for item in self.artifacts.list_metadata() if "run-report" in item.tags]
        items = []
        for metadata in records:
            info = metadata.metadata
            if status and str(info.get("status") or "") != status:
                continue
            if scenario_id and str(info.get("scenario_id") or "") != scenario_id:
                continue
            items.append({
                "run_id": info.get("run_id"),
                "experiment_id": info.get("experiment_id"),
                "scenario_id": info.get("scenario_id"),
                "status": info.get("status"),
                "backend_mode": info.get("backend_mode"),
                "artifact_id": metadata.artifact_ref.artifact_id,
                "created_at": metadata.artifact_ref.created_at.isoformat(),
            })
        items.sort(key=lambda item: (item["created_at"], str(item["run_id"])), reverse=True)
        total = len(items)
        page = items[offset:offset + limit]
        return {
            "schema_version": "1.0",
            "items": page,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(page) if len(page) == limit else None,
            "total": total,
        }

    def load_report_artifact(self, run_id: str) -> ArtifactMetadata | None:
        """Find the persisted report artifact for a run id, or None."""
        for metadata in self.artifacts.list_metadata():
            if "run-report" in metadata.tags and metadata.metadata.get("run_id") == run_id:
                return metadata
        return None
