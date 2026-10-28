"""SQLite-backed immutable preregistration and analysis registry."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any

from agentguard.contracts.research import (
    RegisteredStudy,
    StudyAnalysisReport,
    StudyRegistrationRequest,
)


class IdempotencyConflictError(ValueError):
    """An idempotency key was reused with a different canonical request."""


class ResearchRegistryIntegrityError(RuntimeError):
    """A persisted research record cannot be decoded or verified."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ResearchRegistry:
    """Append-only research registry. Registered plans are never updated in place."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS registered_studies (
                    study_id TEXT PRIMARY KEY,
                    registered_at TEXT NOT NULL,
                    registration_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS study_analyses (
                    analysis_id TEXT PRIMARY KEY,
                    study_id TEXT NOT NULL REFERENCES registered_studies(study_id),
                    analyzed_at TEXT NOT NULL,
                    analysis_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_study_analyses_study ON study_analyses(study_id)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS research_idempotency (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resource_id TEXT,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(scope, idempotency_key)
                )"""
            )

    @staticmethod
    def _decode_record(model: type, payload_json: str):
        try:
            return model.model_validate_json(payload_json)
        except (TypeError, ValueError) as exc:
            raise ResearchRegistryIntegrityError("A persisted research record is corrupt") from exc

    @staticmethod
    def _validate_idempotency_key(key: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
            raise ValueError("Idempotency-Key must be 1-128 safe ASCII characters")

    def register(
        self,
        request: StudyRegistrationRequest,
        *,
        idempotency_key: str | None = None,
        actor_role: str = "operator",
    ) -> RegisteredStudy:
        request_payload = request.model_dump(mode="json")
        if idempotency_key is not None:
            self._validate_idempotency_key(idempotency_key)
            return self._register_idempotent(request, request_payload, idempotency_key, actor_role)
        registration_hash = sha256_json(request_payload)
        study = RegisteredStudy(
            **request_payload,
            registration_sha256=registration_hash,
        )
        payload = study.model_dump(mode="json")
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO registered_studies(study_id, registered_at, registration_sha256, payload_json) VALUES(?,?,?,?)",
                (study.study_id, study.registered_at.isoformat(), registration_hash, canonical_json(payload)),
            )
        return study

    def _register_idempotent(
        self,
        request: StudyRegistrationRequest,
        request_payload: dict[str, Any],
        key: str,
        actor_role: str,
    ) -> RegisteredStudy:
        scope = "POST /v1/studies"
        payload_hash = sha256_json(request_payload)
        registration_hash = payload_hash
        study = RegisteredStudy(**request_payload, registration_sha256=registration_hash)
        response_json = canonical_json(study.model_dump(mode="json"))
        now = study.registered_at.isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM research_idempotency WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if row is not None:
                if row["payload_sha256"] != payload_hash:
                    raise IdempotencyConflictError("Idempotency-Key was already used with a different request")
                if row["status"] != "completed" or not row["response_json"]:
                    raise IdempotencyConflictError("The prior idempotent study write needs reconciliation")
                return self._decode_record(RegisteredStudy, row["response_json"])
            connection.execute(
                "INSERT INTO registered_studies(study_id, registered_at, registration_sha256, payload_json) VALUES(?,?,?,?)",
                (study.study_id, now, registration_hash, response_json),
            )
            connection.execute(
                """INSERT INTO research_idempotency
                   (scope,idempotency_key,payload_sha256,actor_role,status,resource_id,response_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (scope, key, payload_hash, actor_role, "completed", study.study_id, response_json, now),
            )
            connection.commit()
        return study

    def get(self, study_id: str) -> RegisteredStudy | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload_json FROM registered_studies WHERE study_id=?", (study_id,)
            ).fetchone()
        return self._decode_record(RegisteredStudy, row["payload_json"]) if row else None

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        *,
        track: str | None = None,
        sort_by: str = "registered_at",
        sort_order: str = "desc",
    ) -> list[RegisteredStudy]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        columns = {"registered_at": "registered_at", "title": "json_extract(payload_json, '$.title') COLLATE NOCASE"}
        if sort_by not in columns or sort_order not in {"asc", "desc"}:
            raise ValueError("unsupported study sort order")
        where = "WHERE json_extract(payload_json, '$.track')=?" if track is not None else ""
        params: tuple[Any, ...] = (track, limit, offset) if track is not None else (limit, offset)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"SELECT payload_json FROM registered_studies {where} "
                f"ORDER BY {columns[sort_by]} {sort_order.upper()}, study_id {sort_order.upper()} LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._decode_record(RegisteredStudy, row["payload_json"]) for row in rows]

    def study_count(self, track: str | None = None) -> int:
        with closing(self._connect()) as connection, connection:
            if track is None:
                row = connection.execute("SELECT COUNT(*) AS total FROM registered_studies").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM registered_studies WHERE json_extract(payload_json, '$.track')=?",
                    (track,),
                ).fetchone()
        return int(row["total"])

    def add_analysis(
        self,
        report: StudyAnalysisReport,
        *,
        idempotency_key: str | None = None,
        request_payload: dict[str, Any] | None = None,
        actor_role: str = "operator",
    ) -> StudyAnalysisReport:
        if idempotency_key is not None:
            if request_payload is None:
                raise ValueError("idempotent analysis requires the canonical request payload")
            self._validate_idempotency_key(idempotency_key)
            return self._add_analysis_idempotent(report, idempotency_key, request_payload, actor_role)
        payload = report.model_dump(mode="json")
        with self._lock, closing(self._connect()) as connection, connection:
            exists = connection.execute(
                "SELECT 1 FROM registered_studies WHERE study_id=? AND registration_sha256=?",
                (report.study_id, report.registration_sha256),
            ).fetchone()
            if not exists:
                raise ValueError("analysis does not reference the registered study hash")
            try:
                connection.execute(
                    "INSERT INTO study_analyses(analysis_id, study_id, analyzed_at, analysis_sha256, payload_json) VALUES(?,?,?,?,?)",
                    (report.analysis_id, report.study_id, report.analyzed_at.isoformat(), report.analysis_sha256, canonical_json(payload)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("analysis_id already exists or study reference is invalid") from exc
        return report

    def get_idempotent_analysis(
        self, study_id: str, key: str, request_payload: dict[str, Any],
    ) -> StudyAnalysisReport | None:
        self._validate_idempotency_key(key)
        scope = f"POST /v1/studies/{study_id}/analyses"
        payload_hash = sha256_json({"study_id": study_id, "request": request_payload})
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM research_idempotency WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
        if row is None:
            return None
        if row["payload_sha256"] != payload_hash:
            raise IdempotencyConflictError("Idempotency-Key was already used with a different request")
        if row["status"] != "completed" or not row["response_json"]:
            raise IdempotencyConflictError("The prior idempotent analysis needs reconciliation")
        return self._decode_record(StudyAnalysisReport, row["response_json"])

    def _add_analysis_idempotent(
        self,
        report: StudyAnalysisReport,
        key: str,
        request_payload: dict[str, Any],
        actor_role: str,
    ) -> StudyAnalysisReport:
        scope = f"POST /v1/studies/{report.study_id}/analyses"
        canonical_request = {"study_id": report.study_id, "request": request_payload}
        payload_hash = sha256_json(canonical_request)
        report_payload = report.model_dump(mode="json")
        response_json = canonical_json(report_payload)
        created_at = report.analyzed_at.isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM research_idempotency WHERE scope=? AND idempotency_key=?",
                (scope, key),
            ).fetchone()
            if row is not None:
                if row["payload_sha256"] != payload_hash:
                    raise IdempotencyConflictError("Idempotency-Key was already used with a different request")
                if row["status"] != "completed" or not row["response_json"]:
                    raise IdempotencyConflictError("The prior idempotent analysis needs reconciliation")
                return self._decode_record(StudyAnalysisReport, row["response_json"])
            exists = connection.execute(
                "SELECT 1 FROM registered_studies WHERE study_id=? AND registration_sha256=?",
                (report.study_id, report.registration_sha256),
            ).fetchone()
            if not exists:
                raise ValueError("analysis does not reference the registered study hash")
            try:
                connection.execute(
                    "INSERT INTO study_analyses(analysis_id, study_id, analyzed_at, analysis_sha256, payload_json) VALUES(?,?,?,?,?)",
                    (report.analysis_id, report.study_id, created_at, report.analysis_sha256, response_json),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("analysis_id already exists or study reference is invalid") from exc
            connection.execute(
                """INSERT INTO research_idempotency
                   (scope,idempotency_key,payload_sha256,actor_role,status,resource_id,response_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (scope, key, payload_hash, actor_role, "completed", report.analysis_id, response_json, created_at),
            )
            connection.commit()
        return report

    def analyses(
        self,
        study_id: str,
        limit: int = 100,
        offset: int = 0,
        *,
        evidence_mode: str | None = None,
        sort_order: str = "asc",
    ) -> list[StudyAnalysisReport]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        if sort_order not in {"asc", "desc"}:
            raise ValueError("unsupported analysis sort order")
        where = "study_id=? AND json_extract(payload_json, '$.evidence_mode')=?" if evidence_mode else "study_id=?"
        params: tuple[Any, ...] = (
            (study_id, evidence_mode, limit, offset)
            if evidence_mode else (study_id, limit, offset)
        )
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"SELECT payload_json FROM study_analyses WHERE {where} "
                f"ORDER BY analyzed_at {sort_order.upper()}, analysis_id {sort_order.upper()} LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._decode_record(StudyAnalysisReport, row["payload_json"]) for row in rows]

    def analysis_count(self, study_id: str, evidence_mode: str | None = None) -> int:
        with closing(self._connect()) as connection, connection:
            if evidence_mode is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM study_analyses WHERE study_id=?", (study_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM study_analyses WHERE study_id=? AND json_extract(payload_json, '$.evidence_mode')=?",
                    (study_id, evidence_mode),
                ).fetchone()
        return int(row["total"])

    def verify_registration(self, study_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT payload_json FROM registered_studies WHERE study_id=?", (study_id,)
            ).fetchone()
        if row is None:
            return False
        # Hash the exact immutable payload stored at registration time. Rehydrating
        # through a newer contract would inject optional defaults and invalidate
        # valid registrations created by older AgentGuard versions.
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return False
        recorded = payload.pop("registration_sha256", None)
        payload.pop("study_id", None)
        payload.pop("registered_at", None)
        payload.pop("status", None)
        return recorded == sha256_json(payload)
