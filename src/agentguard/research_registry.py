"""SQLite-backed immutable preregistration and analysis registry."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from agentguard.contracts.research import (
    RegisteredStudy,
    StudyAnalysisReport,
    StudyRegistrationRequest,
)


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
        with self._connect() as connection:
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

    def register(self, request: StudyRegistrationRequest) -> RegisteredStudy:
        request_payload = request.model_dump(mode="json")
        registration_hash = sha256_json(request_payload)
        study = RegisteredStudy(
            **request_payload,
            registration_sha256=registration_hash,
        )
        payload = study.model_dump(mode="json")
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO registered_studies(study_id, registered_at, registration_sha256, payload_json) VALUES(?,?,?,?)",
                (study.study_id, study.registered_at.isoformat(), registration_hash, canonical_json(payload)),
            )
        return study

    def get(self, study_id: str) -> RegisteredStudy | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM registered_studies WHERE study_id=?", (study_id,)
            ).fetchone()
        return RegisteredStudy.model_validate_json(row["payload_json"]) if row else None

    def list(self, limit: int = 100) -> list[RegisteredStudy]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM registered_studies ORDER BY registered_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [RegisteredStudy.model_validate_json(row["payload_json"]) for row in rows]

    def add_analysis(self, report: StudyAnalysisReport) -> None:
        payload = report.model_dump(mode="json")
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM registered_studies WHERE study_id=? AND registration_sha256=?",
                (report.study_id, report.registration_sha256),
            ).fetchone()
            if not exists:
                raise ValueError("analysis does not reference the registered study hash")
            connection.execute(
                "INSERT INTO study_analyses(analysis_id, study_id, analyzed_at, analysis_sha256, payload_json) VALUES(?,?,?,?,?)",
                (report.analysis_id, report.study_id, report.analyzed_at.isoformat(), report.analysis_sha256, canonical_json(payload)),
            )

    def analyses(self, study_id: str) -> list[StudyAnalysisReport]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM study_analyses WHERE study_id=? ORDER BY analyzed_at",
                (study_id,),
            ).fetchall()
        return [StudyAnalysisReport.model_validate_json(row["payload_json"]) for row in rows]

    def verify_registration(self, study_id: str) -> bool:
        study = self.get(study_id)
        if study is None:
            return False
        payload = study.model_dump(mode="json")
        recorded = payload.pop("registration_sha256", None)
        payload.pop("study_id", None)
        payload.pop("registered_at", None)
        payload.pop("status", None)
        return recorded == sha256_json(payload)
