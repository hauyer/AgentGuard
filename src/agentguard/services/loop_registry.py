"""Durable registry for public investigation loops (E2).

One additive table in the existing workflow SQLite store maps an idempotent
``loop_id`` to its persisted report artifact. The registry stores no decision
content — the loop report artifact remains the single source of truth, so a
restart recovers queries without a second data model.
"""
from __future__ import annotations

import json
from typing import Any

from agentguard.services.warden_workflow_store import WardenWorkflowStore


class InvestigationLoopRegistry:
    """Create/list/read loop registrations over the workflow store."""

    def __init__(self, workflows: WardenWorkflowStore) -> None:
        self.workflows = workflows
        self._initialize()

    def _initialize(self) -> None:
        with self.workflows._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS investigation_loops (
                loop_id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                base_request_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                phase TEXT NOT NULL,
                stop_reason TEXT,
                request_hash TEXT NOT NULL,
                request_json TEXT NOT NULL DEFAULT '{}',
                report_artifact_id TEXT NOT NULL,
                execution_state TEXT NOT NULL DEFAULT 'unknown',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(investigation_loops)")
            }
            if "execution_state" not in columns:
                # Legacy CREATED/pending rows may already have executed actions.
                # Keep their state unknown so recovery never repeats them blindly.
                db.execute(
                    "ALTER TABLE investigation_loops ADD COLUMN "
                    "execution_state TEXT NOT NULL DEFAULT 'unknown'"
                )
            if "request_json" not in columns:
                db.execute(
                    "ALTER TABLE investigation_loops ADD COLUMN "
                    "request_json TEXT NOT NULL DEFAULT '{}'"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_loops_base "
                "ON investigation_loops(base_request_id, created_at)"
            )

    def register(
        self,
        *,
        loop_id: str,
        idempotency_key: str | None,
        base_request_id: str,
        strategy: str,
        phase: str,
        stop_reason: str | None,
        request_payload: dict[str, Any],
        report_artifact_id: str,
        created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        """Insert or return the existing registration; bool means created.

        A repeated idempotency key with a different payload is a conflict.
        Concurrent inserts on the same key race at the UNIQUE constraint; the
        loser re-reads and returns the winner's registration.
        """
        request_hash = WardenWorkflowStore.canonical_hash(request_payload)
        with self.workflows._connect() as db:
            row = None
            if idempotency_key:
                row = db.execute(
                    "SELECT * FROM investigation_loops WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT * FROM investigation_loops WHERE loop_id=?", (loop_id,)
                ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise ValueError("LOOP_IDEMPOTENCY_CONFLICT")
                return self._decode(row), False
            try:
                db.execute(
                    "INSERT INTO investigation_loops (loop_id, idempotency_key, "
                    "base_request_id, strategy, phase, stop_reason, request_hash, "
                    "request_json, report_artifact_id, execution_state, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_started', ?, ?)",
                    (loop_id, idempotency_key, base_request_id, strategy, phase,
                     stop_reason, request_hash,
                     json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                     report_artifact_id,
                     created_at, created_at),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" not in str(exc):
                    raise
                if not idempotency_key:
                    raise
                row = db.execute(
                    "SELECT * FROM investigation_loops WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row is None:
                    raise
                if row["request_hash"] != request_hash:
                    raise ValueError("LOOP_IDEMPOTENCY_CONFLICT") from exc
                return self._decode(row), False
        row = self._row(loop_id)
        assert row is not None
        return self._decode(row), True

    def update_outcome(
        self, loop_id: str, *, phase: str, stop_reason: str | None,
        report_artifact_id: str, updated_at: str,
        execution_state: str | None = None,
    ) -> None:
        with self.workflows._connect() as db:
            db.execute(
                "UPDATE investigation_loops SET phase=?, stop_reason=?, "
                "report_artifact_id=?, execution_state=COALESCE(?, execution_state), "
                "updated_at=? WHERE loop_id=?",
                (phase, stop_reason, report_artifact_id, execution_state,
                 updated_at, loop_id),
            )

    def get(self, loop_id: str) -> dict[str, Any] | None:
        row = self._row(loop_id)
        return self._decode(row) if row else None

    def list(
        self, *, base_request_id: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("loop pagination out of range")
        with self.workflows._connect() as db:
            if base_request_id:
                rows = db.execute(
                    "SELECT * FROM investigation_loops WHERE base_request_id=? "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (base_request_id, limit, offset),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM investigation_loops "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [self._decode(row) for row in rows]

    def workflows_for_loop_base(self, base_request_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return deterministic audit workflow IDs tied to this base investigation."""
        prefix = f"{base_request_id}-audit-r"
        with self.workflows._connect() as db:
            rows = db.execute(
                "SELECT * FROM warden_workflows WHERE substr(request_id, 1, ?) = ? "
                "ORDER BY request_id LIMIT ?",
                (len(prefix), prefix, limit),
            ).fetchall()
        return [self.workflows._decode(row) for row in rows]

    def _row(self, loop_id: str):
        with self.workflows._connect() as db:
            return db.execute(
                "SELECT * FROM investigation_loops WHERE loop_id=?", (loop_id,)
            ).fetchone()

    @staticmethod
    def _decode(row) -> dict[str, Any]:
        return {
            "loop_id": row["loop_id"],
            "idempotency_key": row["idempotency_key"],
            "base_request_id": row["base_request_id"],
            "strategy": row["strategy"],
            "phase": row["phase"],
            "stop_reason": row["stop_reason"],
            "request_payload": json.loads(row["request_json"]),
            "report_artifact_id": row["report_artifact_id"],
            "execution_state": row["execution_state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
