"""SQLite-backed, versioned experience memory with split and reward gates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, ClassVar
from uuid import uuid4

from agentguard.contracts.experience import ExperienceRecommendationPolicy, ExperienceRecord

DEFAULT_POLICY = ExperienceRecommendationPolicy(policy_version="experience-thompson-v1")


class ExperiencePartitionConflict(ValueError):
    """Raised when the same case family is assigned to more than one split."""


class ExperienceStore:
    """Existing experience database, upgraded in place with fail-closed migrations."""

    _NEW_COLUMNS: ClassVar[dict[str, str]] = {
        "dataset_partition": "TEXT NOT NULL DEFAULT 'held_out_family'",
        "model_version": "TEXT NOT NULL DEFAULT 'unknown'",
        "policy_version": "TEXT NOT NULL DEFAULT 'unknown'",
        "evidence_grade": "TEXT NOT NULL DEFAULT 'observational'",
        "verified_at": "TEXT",
        "expires_at": "TEXT",
        "audit_run_id": "TEXT",
    }

    def __init__(self, path: str | Path = ".agentguard/experiences.sqlite3") -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as db:
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
                payload_json TEXT NOT NULL,
                dataset_partition TEXT NOT NULL DEFAULT 'held_out_family',
                model_version TEXT NOT NULL DEFAULT 'unknown',
                policy_version TEXT NOT NULL DEFAULT 'unknown',
                evidence_grade TEXT NOT NULL DEFAULT 'observational',
                verified_at TEXT,
                expires_at TEXT,
                audit_run_id TEXT
            )""")
            existing_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(experiences)").fetchall()
            }
            for column, definition in self._NEW_COLUMNS.items():
                if column not in existing_columns:
                    db.execute(f"ALTER TABLE experiences ADD COLUMN {column} {definition}")

            db.execute("""CREATE TABLE IF NOT EXISTS case_family_partitions (
                case_family TEXT PRIMARY KEY,
                dataset_partition TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS experience_policy_versions (
                policy_version TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS experience_policy_state (
                policy_key TEXT PRIMARY KEY,
                policy_version TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS experience_policy_events (
                event_id TEXT PRIMARY KEY,
                policy_version TEXT NOT NULL,
                previous_version TEXT,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_scenario ON experiences(scenario_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_risk ON experiences(risk_type)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_verified ON experiences(verification_status, allow_auto_recommendation)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_context ON experiences(dataset_partition, scenario_id, risk_type, action_type)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_experience_versions ON experiences(model_version, policy_version, evidence_grade)")

            # Old records have no trustworthy dataset split. Quarantine them in place.
            rows = db.execute("SELECT experience_id, case_family, payload_json FROM experiences").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    payload = None
                if not isinstance(payload, dict):
                    continue
                if "dataset_partition" not in payload:
                    payload["dataset_partition"] = "held_out_family"
                db.execute(
                    "UPDATE experiences SET dataset_partition=?, model_version=?, policy_version=?, evidence_grade=?, "
                    "verified_at=?, expires_at=?, audit_run_id=?, payload_json=? WHERE experience_id=?",
                    (
                        payload.get("dataset_partition", "held_out_family"),
                        payload.get("model_version", "unknown"),
                        payload.get("policy_version", "unknown"),
                        payload.get("evidence_grade", "observational"),
                        payload.get("verified_at"), payload.get("expires_at"),
                        payload.get("audit_run_id"),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        row["experience_id"],
                    ),
                )

            family_rows = db.execute(
                "SELECT case_family, GROUP_CONCAT(DISTINCT dataset_partition) AS partitions FROM experiences GROUP BY case_family"
            ).fetchall()
            for row in family_rows:
                partitions = set((row["partitions"] or "held_out_family").split(","))
                assigned = next(iter(partitions)) if len(partitions) == 1 else "held_out_family"
                if len(partitions) > 1:
                    conflicting = db.execute(
                        "SELECT experience_id, payload_json FROM experiences WHERE case_family=?",
                        (row["case_family"],),
                    ).fetchall()
                    for item in conflicting:
                        payload = json.loads(item["payload_json"])
                        payload["dataset_partition"] = "held_out_family"
                        db.execute(
                            "UPDATE experiences SET dataset_partition='held_out_family', payload_json=? WHERE experience_id=?",
                            (json.dumps(payload, ensure_ascii=False, sort_keys=True), item["experience_id"]),
                        )
                db.execute(
                    "INSERT OR IGNORE INTO case_family_partitions(case_family, dataset_partition) VALUES (?, ?)",
                    (row["case_family"], assigned),
                )

            default_payload = DEFAULT_POLICY.model_dump_json()
            db.execute(
                "INSERT OR IGNORE INTO experience_policy_versions(policy_version, payload_json, created_at) VALUES (?, ?, ?)",
                (DEFAULT_POLICY.policy_version, default_payload, DEFAULT_POLICY.created_at.isoformat()),
            )
            db.execute(
                "INSERT OR IGNORE INTO experience_policy_state(policy_key, policy_version, updated_at) VALUES ('recommendation', ?, ?)",
                (DEFAULT_POLICY.policy_version, datetime.now().astimezone().isoformat()),
            )
            db.execute("PRAGMA user_version=2")

    def add(self, record: ExperienceRecord) -> ExperienceRecord:
        payload = record.model_dump(mode="json")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing_family = db.execute(
                "SELECT dataset_partition FROM case_family_partitions WHERE case_family=?",
                (record.case_family,),
            ).fetchone()
            if existing_family and existing_family["dataset_partition"] != record.dataset_partition:
                raise ExperiencePartitionConflict(
                    f"case family {record.case_family!r} is already assigned to {existing_family['dataset_partition']}"
                )
            db.execute(
                "INSERT OR IGNORE INTO case_family_partitions(case_family, dataset_partition) VALUES (?, ?)",
                (record.case_family, record.dataset_partition),
            )
            db.execute("""INSERT INTO experiences (
                experience_id, scenario_id, case_family, risk_type, action_type,
                verification_status, outcome, allow_auto_recommendation, created_at,
                payload_json, dataset_partition, model_version, policy_version,
                evidence_grade, verified_at, expires_at, audit_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                record.experience_id, record.scenario_id, record.case_family, record.risk_type,
                record.action_type, record.verification_status, record.outcome,
                int(record.allow_auto_recommendation), record.created_at.isoformat(), serialized,
                record.dataset_partition, record.model_version, record.policy_version,
                record.evidence_grade, record.verified_at.isoformat() if record.verified_at else None,
                record.expires_at.isoformat() if record.expires_at else None, record.audit_run_id,
            ))
        return record

    def get(self, experience_id: str) -> ExperienceRecord | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT payload_json FROM experiences WHERE experience_id=?", (experience_id,)
            ).fetchone()
        return ExperienceRecord.model_validate_json(row["payload_json"]) if row else None

    def search(
        self,
        query: str = "",
        *,
        scenario_id: str | None = None,
        risk_type: str | None = None,
        action_type: str | None = None,
        model_version: str | None = None,
        policy_version: str | None = None,
        evidence_grade: str | None = None,
        verified_after: datetime | None = None,
        dataset_partition: str | None = None,
        verified_only: bool = False,
        exclude_case_family: str | None = None,
        include_evaluation_partitions: bool = False,
        limit: int = 50,
    ) -> list[ExperienceRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if dataset_partition not in {None, "training", "validation", "test", "held_out_family"}:
            raise ValueError("invalid dataset_partition")
        clauses: list[str] = []
        params: list[Any] = []
        if not include_evaluation_partitions:
            if dataset_partition in {"test", "held_out_family"}:
                return []
            if dataset_partition:
                clauses.append("dataset_partition=?")
                params.append(dataset_partition)
            else:
                clauses.append("dataset_partition IN ('training', 'validation')")
        elif dataset_partition:
            clauses.append("dataset_partition=?")
            params.append(dataset_partition)
        for column, value in (
            ("scenario_id", scenario_id),
            ("risk_type", risk_type),
            ("action_type", action_type),
            ("model_version", model_version),
            ("policy_version", policy_version),
            ("evidence_grade", evidence_grade),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if verified_after:
            clauses.append("verified_at>=?")
            params.append(verified_after.isoformat())
        if verified_only:
            clauses.append("verification_status='verified'")
        if exclude_case_family:
            clauses.append("case_family<>?")
            params.append(exclude_case_family)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM experiences" + where + " ORDER BY created_at DESC",
                params,
            ).fetchall()
        records = [ExperienceRecord.model_validate_json(row["payload_json"]) for row in rows]
        terms = [part.lower() for part in query.split() if part.strip()]
        if terms:
            def relevance(record: ExperienceRecord) -> int:
                haystack = " ".join([
                    record.scenario_id, record.case_family, record.risk_type,
                    record.hypothesis_summary, record.action_type, record.action_summary,
                    " ".join(record.tags),
                ]).lower()
                return sum(term in haystack for term in terms)

            records = [record for record in records if relevance(record) > 0]
            records.sort(
                key=lambda record: (
                    relevance(record), record.verification_status == "verified", record.created_at
                ),
                reverse=True,
            )
        return records[:limit]

    def conflict_groups(self) -> list[dict[str, Any]]:
        """Find contradictory labels attached to the same run/evidence context."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM experiences WHERE dataset_partition='training' ORDER BY created_at"
            ).fetchall()
        records = [ExperienceRecord.model_validate_json(row["payload_json"]) for row in rows]
        groups: dict[tuple[Any, ...], list[ExperienceRecord]] = {}
        for record in records:
            if record.schema_version != "0.2" or not record.audit_run_id or not record.evidence_refs:
                continue
            key = (
                record.scenario_id, record.risk_type, record.case_family, record.action_type,
                record.audit_run_id, tuple(sorted(record.evidence_refs)), record.model_version,
                record.policy_version,
            )
            groups.setdefault(key, []).append(record)
        conflicts = []
        for key, items in groups.items():
            outcomes = sorted({item.outcome for item in items})
            if len(outcomes) < 2:
                continue
            ids = sorted(item.experience_id for item in items)
            conflicts.append({
                "conflict_id": "conflict-" + hashlib.sha256(repr(key).encode()).hexdigest()[:16],
                "experience_ids": ids,
                "outcomes": outcomes,
                "reason": "The same run and evidence references carry different outcome labels.",
                "resolution_status": "unresolved",
            })
        return conflicts

    def conflicted_experience_ids(self) -> set[str]:
        return {
            experience_id
            for conflict in self.conflict_groups()
            for experience_id in conflict["experience_ids"]
        }

    def reward_records(
        self,
        *,
        scenario_id: str | None = None,
        risk_type: str | None = None,
        case_family: str | None = None,
        action_type: str | None = None,
        model_version: str | None = None,
        policy_version: str | None = None,
        evidence_grade: str | None = None,
        verified_after: datetime | None = None,
        limit: int = 500,
    ) -> list[ExperienceRecord]:
        conflicts = self.conflicted_experience_ids()
        records = self.search(
            scenario_id=scenario_id,
            risk_type=risk_type,
            action_type=action_type,
            model_version=model_version,
            policy_version=policy_version,
            evidence_grade=evidence_grade,
            verified_after=verified_after,
            dataset_partition="training",
            verified_only=True,
            exclude_case_family=case_family,
            limit=limit,
        )
        return [
            record for record in records
            if not record.reward_ineligibility_reasons(conflicted=record.experience_id in conflicts)
        ]

    def recommendations(
        self,
        *,
        scenario_id: str | None = None,
        risk_type: str | None = None,
        action_type: str | None = None,
        model_version: str | None = None,
        policy_version: str | None = None,
        evidence_grade: str | None = None,
        verified_after: datetime | None = None,
        case_family: str | None = None,
        limit: int = 5,
    ) -> list[ExperienceRecord]:
        records = self.reward_records(
            scenario_id=scenario_id,
            risk_type=risk_type,
            action_type=action_type,
            model_version=model_version,
            policy_version=policy_version,
            evidence_grade=evidence_grade,
            verified_after=verified_after,
            case_family=case_family,
        )
        records.sort(key=lambda item: (
            item.evidence_grade == "causal_validated",
            item.verified_at or item.created_at,
            item.repetitions,
        ), reverse=True)
        return records[:max(1, min(50, limit))]

    def action_statistics(self, *, verified_only: bool = True) -> list[dict[str, Any]]:
        # Descriptive statistics are restricted to the same strict gate as reward use.
        records = self.reward_records(limit=500)
        grouped: dict[str, list[ExperienceRecord]] = {}
        for record in records:
            grouped.setdefault(record.action_type, []).append(record)
        return [{
            "action_type": action_type,
            "n": len(items),
            "success_rate": sum(item.outcome == "success" for item in items) / len(items),
            "eligible_records_only": True,
            "verified_only": True,
        } for action_type, items in sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True)]

    def save_policy(self, policy: ExperienceRecommendationPolicy, *, activate: bool = False) -> ExperienceRecommendationPolicy:
        payload = policy.model_dump(mode="json")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connection() as db:
            existing = db.execute(
                "SELECT payload_json FROM experience_policy_versions WHERE policy_version=?",
                (policy.policy_version,),
            ).fetchone()
            if existing and json.loads(existing["payload_json"]) != payload:
                raise ValueError("recommendation policy versions are immutable")
            db.execute(
                "INSERT OR IGNORE INTO experience_policy_versions(policy_version, payload_json, created_at) VALUES (?, ?, ?)",
                (policy.policy_version, serialized, policy.created_at.isoformat()),
            )
            if activate:
                self._activate_policy_in_transaction(db, policy.policy_version, "activated", "registered_and_activated")
        return policy

    @staticmethod
    def _activate_policy_in_transaction(
        db: sqlite3.Connection, policy_version: str, action: str, reason: str
    ) -> None:
        policy = db.execute(
            "SELECT policy_version FROM experience_policy_versions WHERE policy_version=?",
            (policy_version,),
        ).fetchone()
        if policy is None:
            raise KeyError("recommendation policy version not found")
        current = db.execute(
            "SELECT policy_version FROM experience_policy_state WHERE policy_key='recommendation'"
        ).fetchone()
        previous = current["policy_version"] if current else None
        if previous == policy_version:
            return
        now = datetime.now().astimezone().isoformat()
        db.execute(
            "INSERT INTO experience_policy_state(policy_key, policy_version, updated_at) VALUES ('recommendation', ?, ?) "
            "ON CONFLICT(policy_key) DO UPDATE SET policy_version=excluded.policy_version, updated_at=excluded.updated_at",
            (policy_version, now),
        )
        db.execute(
            "INSERT INTO experience_policy_events(event_id, policy_version, previous_version, action, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("policy-event-" + uuid4().hex[:16], policy_version, previous, action, reason, now),
        )

    def activate_policy(self, policy_version: str, *, reason: str = "operator_selected") -> None:
        with self._lock, self._connection() as db:
            self._activate_policy_in_transaction(db, policy_version, "activated", reason)

    def rollback_policy(self, policy_version: str, *, reason: str = "operator_rollback") -> None:
        with self._lock, self._connection() as db:
            self._activate_policy_in_transaction(db, policy_version, "rollback", reason)

    def policy(self, policy_version: str | None = None) -> ExperienceRecommendationPolicy:
        with self._connection() as db:
            if policy_version:
                row = db.execute(
                    "SELECT payload_json FROM experience_policy_versions WHERE policy_version=?",
                    (policy_version,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT p.payload_json FROM experience_policy_state s JOIN experience_policy_versions p "
                    "ON p.policy_version=s.policy_version WHERE s.policy_key='recommendation'"
                ).fetchone()
        if row is None:
            if policy_version:
                raise KeyError("recommendation policy version not found")
            return DEFAULT_POLICY
        return ExperienceRecommendationPolicy.model_validate_json(row["payload_json"])

    def active_policy(self) -> ExperienceRecommendationPolicy:
        return self.policy()

    def policy_versions(self) -> list[ExperienceRecommendationPolicy]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM experience_policy_versions ORDER BY created_at, policy_version"
            ).fetchall()
        return [ExperienceRecommendationPolicy.model_validate_json(row["payload_json"]) for row in rows]

    def policy_history(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT event_id, policy_version, previous_version, action, reason, created_at "
                "FROM experience_policy_events ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        with self._connection() as db:
            return int(db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0])
