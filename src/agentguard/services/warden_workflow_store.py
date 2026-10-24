"""Durable idempotency journal for upper-layer WARDEN workflows.

The journal intentionally records control-plane state, not model tensors or private
prompt content. It prevents a retried AgentGuard request from blindly submitting a
second WARDEN run when the first submission may have succeeded but its response was
lost. Ambiguous submission states require reconciliation rather than auto-retry.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from agentguard.services.workflow_state import (
    TERMINAL_JOURNAL_STATES,
    WorkflowTransitionError,
    project_workflow_state,
    validate_transition,
)


class WorkflowConflictError(ValueError):
    """A request ID was reused for a different canonical request payload."""


class WardenWorkflowStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path = ".agentguard/warden_workflows.sqlite3") -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        # Keep the SQLite default journal mode here. Setting WAL on every new
        # connection races when several workers initialize the store concurrently.
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def execution_lock(self, request_id: str) -> Iterator[bool]:
        """Hold a local OS lock for one workflow, released automatically on process exit.

        Lock files contain no request content and are never deleted while active.
        They coordinate local processes sharing this SQLite path, not remote hosts.
        """
        directory = self.path.parent / (self.path.name + ".locks")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        with path.open("a+b") as handle:
            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        request = json.loads(row["request_json"])
        context = json.loads(row["context_json"])
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "request_id": row["request_id"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "request": request,
            "context": context,
            "result": result,
            "workflow_status": project_workflow_state(row["status"], context, result).value,
            "plan_id": (context.get("plan") or {}).get("plan_id"),
            "run_id": (context.get("run") or {}).get("run_id"),
            "authorization_ref": request.get("authorization_ref"),
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
            validate_transition(old_status, status)
            if old_status in TERMINAL_JOURNAL_STATES:
                existing = self._decode(row)
                if (
                    (result is not None and dict(result) != existing["result"])
                    or (context is not None and any(
                        existing["context"].get(key) != value for key, value in context.items()
                    ))
                ):
                    raise WorkflowTransitionError("Terminal workflow data cannot be overwritten")
                return existing
            old_context = json.loads(row["context_json"])
            if context is not None:
                old_context.update(context)
            result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if result is not None else row["result_json"]
            db.execute("""UPDATE warden_workflows SET status=?, context_json=?, result_json=?, error_code=?, error_message=?, revision=revision+1, updated_at=? WHERE request_id=?""",
                (status, json.dumps(old_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")), result_json, error_code, error_message, now, request_id))
            event_detail = dict(detail or {})
            if error_code:
                event_detail["error_code"] = error_code
            db.execute("INSERT INTO warden_workflow_events(request_id, from_status, to_status, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (request_id, old_status, status, json.dumps(event_detail, ensure_ascii=False, sort_keys=True), now))
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

    def record_processing_attempt(self, request_id: str) -> None:
        """Append an attempt under the caller's execution lock; terminal replays are read-only."""
        entry = self.get(request_id)
        if entry is None:
            raise KeyError(request_id)
        if entry["status"] in TERMINAL_JOURNAL_STATES:
            return
        self.update(request_id, entry["status"],
                    error_code=entry["error_code"], error_message=entry["error_message"], detail={
            "operation": "process_workflow", "is_retry": entry["revision"] > 0,
            "is_recovery": entry["status"] not in {"created", "preflighting"},
        })

    def execution_statistics(self, request_id: str) -> dict[str, Any]:
        """Derive counters from the existing durable events without a second database."""
        events = self.events(request_id)
        attempts = [e["detail"] for e in events if e["detail"].get("operation") == "process_workflow"]
        return {
            "processing_attempts": len(attempts),
            "retry_count": sum(bool(item.get("is_retry")) for item in attempts),
            "recovery_count": sum(bool(item.get("is_recovery")) for item in attempts),
            "plan_submission_attempts": sum(
                e["detail"].get("operation") == "create_audit_plan" for e in events
            ),
            "run_submission_attempts": sum(
                e["detail"].get("operation") == "start_run" for e in events
            ),
            "legacy_attempt_history": not attempts or all(item.get("is_retry") for item in attempts),
        }
