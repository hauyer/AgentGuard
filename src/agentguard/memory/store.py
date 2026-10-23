"""SQLite-backed, versioned experience memory with verified-only recommendations."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any
from agentguard.contracts.experience import ExperienceRecord


class ExperienceStore:
    def __init__(self, path: str | Path = ".agentguard/experiences.sqlite3") -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS experiences (
                experience_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                case_family TEXT NOT NULL,
                risk_type TEXT NOT NULL,
                action_type TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                outcome TEXT NOT NULL,
                allow_auto_recommendation INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_scenario ON experiences(scenario_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_risk ON experiences(risk_type)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_verified ON experiences(verification_status, allow_auto_recommendation)")

    def add(self, record: ExperienceRecord) -> ExperienceRecord:
        payload = record.model_dump(mode="json")
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO experiences VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                record.experience_id, record.scenario_id, record.case_family, record.risk_type,
                record.action_type, record.verification_status, record.outcome,
                int(record.allow_auto_recommendation), record.created_at.isoformat(),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ))
        return record

    def get(self, experience_id: str) -> ExperienceRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM experiences WHERE experience_id=?", (experience_id,)).fetchone()
        return ExperienceRecord.model_validate_json(row["payload_json"]) if row else None

    def search(
        self, query: str = "", *, scenario_id: str | None = None, risk_type: str | None = None,
        verified_only: bool = False, limit: int = 50,
    ) -> list[ExperienceRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses=[]; params=[]
        if scenario_id:
            clauses.append("scenario_id=?"); params.append(scenario_id)
        if risk_type:
            clauses.append("risk_type=?"); params.append(risk_type)
        if verified_only:
            clauses.append("verification_status='verified'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM experiences" + where + " ORDER BY created_at DESC", params).fetchall()
        records = [ExperienceRecord.model_validate_json(row["payload_json"]) for row in rows]
        terms = [part.lower() for part in query.split() if part.strip()]
        if terms:
            def relevance(record: ExperienceRecord) -> int:
                haystack = " ".join([record.scenario_id, record.case_family, record.risk_type,
                    record.hypothesis_summary, record.action_type, record.action_summary,
                    " ".join(record.tags)]).lower()
                return sum(term in haystack for term in terms)
            records = [r for r in records if relevance(r) > 0]
            records.sort(key=lambda r: (relevance(r), r.verification_status == "verified", r.created_at), reverse=True)
        return records[:limit]

    def action_statistics(self, *, verified_only: bool = True) -> list[dict[str, Any]]:
        clause = "WHERE verification_status='verified'" if verified_only else ""
        with self._connect() as db:
            rows = db.execute(f"""SELECT action_type, COUNT(*) AS n,
                SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS successes,
                AVG(CASE WHEN json_valid(payload_json) THEN 1.0 ELSE 0.0 END) AS record_integrity
                FROM experiences {clause} GROUP BY action_type ORDER BY n DESC""").fetchall()
        return [{"action_type": r["action_type"], "n": r["n"],
                 "success_rate": (r["successes"] / r["n"]) if r["n"] else 0.0,
                 "verified_only": verified_only} for r in rows]

    def recommendations(self, *, scenario_id: str | None = None, risk_type: str | None = None, limit: int = 5) -> list[ExperienceRecord]:
        # Never promote unverified memories to executable strategy.
        records = self.search(scenario_id=scenario_id, risk_type=risk_type, verified_only=True, limit=500)
        records = [r for r in records if r.allow_auto_recommendation and r.outcome == "success"]
        records.sort(key=lambda r: (r.repetitions, r.evidence_grade == "causal_validated", r.created_at), reverse=True)
        return records[:max(1, min(50, limit))]

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0])
