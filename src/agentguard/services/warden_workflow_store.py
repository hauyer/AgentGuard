"""Durable idempotency journal for upper-layer WARDEN workflows.

The journal intentionally records control-plane state, not model tensors or private
prompt content. It prevents a retried AgentGuard request from blindly submitting a
second WARDEN run when the first submission may have succeeded but its response was
lost. Ambiguous submission states require reconciliation rather than auto-retry.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


class WorkflowConflictError(ValueError):
    """A request ID was reused for a different canonical request payload."""


class WardenWorkflowStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path = ".agentguard/warden_workflows.sqlite3") -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        # Keep the SQLite default journal mode here. Setting WAL on every new
        # connection races when several workers initialize the store concurrently.
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS warden_workflows (
                request_id TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                context_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT,
                error_code TEXT,
                error_message TEXT,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_warden_workflows_status ON warden_workflows(status, updated_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS warden_workflow_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES warden_workflows(request_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_warden_workflow_events_request ON warden_workflow_events(request_id, event_id)")

    @staticmethod
    def canonical_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "request_id": row["request_id"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "context": json.loads(row["context_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def begin(self, request_id: str, request_payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create a workflow or return its existing record; bool indicates creation."""
        if not request_id or not request_id.strip():
            raise ValueError("request_id must be nonblank")
        request_json = json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        request_hash = self.canonical_hash(request_payload)
        now = self._now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM warden_workflows WHERE request_id=?", (request_id,)).fetchone()
            if row:
                existing = self._decode(row)
                if existing["request_hash"] != request_hash:
                    raise WorkflowConflictError(
                        "request_id already exists with a different request payload"
                    )
                db.commit()
                return existing, False
            db.execute("""INSERT INTO warden_workflows
                (request_id, request_hash, status, request_json, context_json, created_at, updated_at)
                VALUES (?, ?, 'created', ?, '{}', ?, ?)""",
                (request_id, request_hash, request_json, now, now))
            db.execute("INSERT INTO warden_workflow_events(request_id, from_status, to_status, detail_json, created_at) VALUES (?, NULL, 'created', '{}', ?)", (request_id, now))
            row = db.execute("SELECT * FROM warden_workflows WHERE request_id=?", (request_id,)).fetchone()
            db.commit()
            return self._decode(row), True

    def update(
        self, request_id: str, status: str, *, context: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None, error_code: str | None = None,
        error_message: str | None = None, detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM warden_workflows WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            old_status = row["status"]
            old_context = json.loads(row["context_json"])
            if context is not None:
                old_context.update(context)
            result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if result is not None else row["result_json"]
            db.execute("""UPDATE warden_workflows SET status=?, context_json=?, result_json=?, error_code=?, error_message=?, revision=revision+1, updated_at=? WHERE request_id=?""",
                (status, json.dumps(old_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")), result_json, error_code, error_message, now, request_id))
            db.execute("INSERT INTO warden_workflow_events(request_id, from_status, to_status, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (request_id, old_status, status, json.dumps(detail or {}, ensure_ascii=False, sort_keys=True), now))
            updated = db.execute("SELECT * FROM warden_workflows WHERE request_id=?", (request_id,)).fetchone()
            db.commit()
            return self._decode(updated)

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM warden_workflows WHERE request_id=?", (request_id,)).fetchone()
        return self._decode(row) if row else None

    def events(self, request_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT event_id, from_status, to_status, detail_json, created_at FROM warden_workflow_events WHERE request_id=? ORDER BY event_id", (request_id,)).fetchall()
        return [{"event_id": r["event_id"], "from_status": r["from_status"], "to_status": r["to_status"], "detail": json.loads(r["detail_json"]), "created_at": r["created_at"]} for r in rows]

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM warden_workflows WHERE status=? ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM warden_workflows ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode(r) for r in rows]

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM warden_workflows").fetchone()[0])
