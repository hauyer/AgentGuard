"""Durable index for persisted strategy evaluation reports (G1).

A lightweight additive table in the workflow SQLite store maps an evaluation
ID to its persisted report artifact. The report itself remains the content-
addressed artifact; the index only enables discovery and listing without
scanning the artifact store.
"""
from __future__ import annotations

from typing import Any

from agentguard.services.warden_workflow_store import WardenWorkflowStore


class EvaluationRegistry:
    """Create/list/read evaluation index entries over the workflow store."""

    def __init__(self, workflows: WardenWorkflowStore) -> None:
        self.workflows = workflows
        self._initialize()

    def _initialize(self) -> None:
        with self.workflows._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS strategy_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                comparison_id TEXT NOT NULL,
                report_artifact_id TEXT NOT NULL,
                data_partition TEXT NOT NULL,
                source_commit TEXT,
                executed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                created_at TEXT NOT NULL
            )""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_partition "
                "ON strategy_evaluations(data_partition, created_at)"
            )

    def register(
        self,
        *,
        evaluation_id: str,
        comparison_id: str,
        report_artifact_id: str,
        data_partition: str,
        source_commit: str | None,
        executed_at: str,
        status: str = "available",
    ) -> None:
        """Insert or replace an evaluation index entry."""
        with self.workflows._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO strategy_evaluations "
                "(evaluation_id, comparison_id, report_artifact_id, data_partition, "
                "source_commit, executed_at, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (evaluation_id, comparison_id, report_artifact_id, data_partition,
                 source_commit, executed_at, status, executed_at),
            )

    def get(self, evaluation_id: str) -> dict[str, Any] | None:
        row = self._row(evaluation_id)
        return self._decode(row) if row else None

    def list(
        self,
        *,
        data_partition: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("evaluation pagination out of range")
        with self.workflows._connect() as db:
            if data_partition:
                rows = db.execute(
                    "SELECT * FROM strategy_evaluations WHERE data_partition=? "
                    "ORDER BY executed_at DESC LIMIT ? OFFSET ?",
                    (data_partition, limit, offset),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM strategy_evaluations "
                    "ORDER BY executed_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [self._decode(row) for row in rows]

    def _row(self, evaluation_id: str):
        with self.workflows._connect() as db:
            return db.execute(
                "SELECT * FROM strategy_evaluations WHERE evaluation_id=?",
                (evaluation_id,),
            ).fetchone()

    @staticmethod
    def _decode(row) -> dict[str, Any]:
        return {
            "evaluation_id": row["evaluation_id"],
            "comparison_id": row["comparison_id"],
            "report_artifact_id": row["report_artifact_id"],
            "data_partition": row["data_partition"],
            "source_commit": row["source_commit"],
            "executed_at": row["executed_at"],
            "status": row["status"],
        }
