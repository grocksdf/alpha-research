from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .models import Candidate, EvaluationResult, SimulationResult, ValidationResult

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    evaluator_hash TEXT NOT NULL,
    git_revision TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    created_at TEXT NOT NULL,
    expression TEXT NOT NULL,
    canonical TEXT,
    expression_hash TEXT,
    family_hash TEXT,
    family TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    rationale TEXT NOT NULL,
    parent_id INTEGER REFERENCES candidates(id),
    parameters_json TEXT NOT NULL,
    validation_json TEXT,
    status TEXT NOT NULL,
    UNIQUE(expression_hash)
);

CREATE TABLE IF NOT EXISTS simulations (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    created_at TEXT NOT NULL,
    simulation_url TEXT,
    alpha_id TEXT,
    status TEXT NOT NULL,
    alpha_json TEXT,
    checks_json TEXT,
    raw_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    created_at TEXT NOT NULL,
    eligible INTEGER NOT NULL,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    components_json TEXT NOT NULL,
    evaluator_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    run_id INTEGER REFERENCES runs(id),
    candidate_id INTEGER REFERENCES candidates(id),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_candidates_family ON candidates(family_hash);
CREATE INDEX IF NOT EXISTS ix_evaluations_score ON evaluations(eligible, score DESC);
CREATE INDEX IF NOT EXISTS ix_simulations_alpha ON simulations(alpha_id);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ResearchMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "git_revision" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN git_revision TEXT")
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def create_run(self, config: dict[str, Any], evaluator_hash: str, note: str = "") -> int:
        try:
            git_revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            git_revision = "uncommitted"
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO runs("
                "created_at,status,config_json,evaluator_hash,git_revision,note"
                ") VALUES(?,?,?,?,?,?)",
                (
                    utc_now(),
                    "RUNNING",
                    json.dumps(config),
                    evaluator_hash,
                    git_revision,
                    note,
                ),
            )
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str = "COMPLETE") -> None:
        with self.transaction() as db:
            db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))

    def add_candidate(
        self,
        run_id: int,
        candidate: Candidate,
        validation: ValidationResult,
    ) -> tuple[int, bool]:
        expression_hash = (
            __import__("hashlib")
            .sha256((validation.canonical or candidate.expression).encode())
            .hexdigest()
        )
        with self.transaction() as db:
            parent_id = candidate.parent_id
            if parent_id is not None:
                parent = db.execute("SELECT 1 FROM candidates WHERE id=?", (parent_id,)).fetchone()
                if parent is None:
                    parent_id = None
            existing = db.execute(
                "SELECT id FROM candidates WHERE expression_hash=?", (expression_hash,)
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            cursor = db.execute(
                """
                INSERT INTO candidates(
                    run_id,created_at,expression,canonical,expression_hash,family_hash,
                    family,hypothesis,rationale,parent_id,parameters_json,validation_json,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    utc_now(),
                    candidate.expression,
                    validation.canonical,
                    expression_hash,
                    validation.family_hash,
                    candidate.family,
                    candidate.hypothesis,
                    candidate.rationale,
                    parent_id,
                    json.dumps(candidate.parameters),
                    json.dumps(validation.to_dict()),
                    "VALIDATED" if validation.valid else "REJECTED",
                ),
            )
        return int(cursor.lastrowid), True

    def record_simulation(
        self,
        candidate_id: int,
        result: SimulationResult | None = None,
        error: str | None = None,
    ) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO simulations(
                    candidate_id,created_at,simulation_url,alpha_id,status,
                    alpha_json,checks_json,raw_json,error
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    utc_now(),
                    result.simulation_url if result else None,
                    result.alpha_id if result else None,
                    "COMPLETE" if result else "FAILED",
                    json.dumps(result.alpha) if result else None,
                    json.dumps(result.checks) if result else None,
                    json.dumps(result.raw_simulation) if result else None,
                    error,
                ),
            )
            db.execute(
                "UPDATE candidates SET status=? WHERE id=?",
                ("SIMULATED" if result else "SIMULATION_FAILED", candidate_id),
            )
        return int(cursor.lastrowid)

    def record_evaluation(
        self,
        candidate_id: int,
        result: EvaluationResult,
        evaluator_hash: str,
    ) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO evaluations(
                    candidate_id,created_at,eligible,score,reasons_json,
                    metrics_json,components_json,evaluator_hash
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    utc_now(),
                    int(result.eligible),
                    result.score,
                    json.dumps(result.reasons),
                    json.dumps(result.metrics),
                    json.dumps(result.components),
                    evaluator_hash,
                ),
            )
            db.execute(
                "UPDATE candidates SET status=? WHERE id=?",
                ("READY" if result.eligible else "EVALUATED", candidate_id),
            )
        return int(cursor.lastrowid)

    def event(
        self,
        kind: str,
        payload: dict[str, Any],
        run_id: int | None = None,
        candidate_id: int | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO events(created_at,run_id,candidate_id,kind,payload_json) "
                "VALUES(?,?,?,?,?)",
                (utc_now(), run_id, candidate_id, kind, json.dumps(payload)),
            )

    def candidate_exists(self, canonical: str) -> bool:
        expression_hash = __import__("hashlib").sha256(canonical.encode()).hexdigest()
        row = self.connection.execute(
            "SELECT 1 FROM candidates WHERE expression_hash=?", (expression_hash,)
        ).fetchone()
        return row is not None

    def recent_memory(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT c.id,c.canonical,c.family,c.hypothesis,c.status,
                   e.eligible,e.score,e.reasons_json,e.metrics_json
            FROM candidates c
            LEFT JOIN evaluations e ON e.id=(
                SELECT id FROM evaluations e2 WHERE e2.candidate_id=c.id
                ORDER BY e2.id DESC LIMIT 1
            )
            ORDER BY c.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("reasons_json", "metrics_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key) or "null")
            result.append(item)
        return result

    def ready_candidates(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT c.id,c.expression,c.canonical,c.family,c.hypothesis,
                   s.alpha_id,e.score,e.metrics_json,e.reasons_json
            FROM candidates c
            JOIN simulations s ON s.candidate_id=c.id AND s.status='COMPLETE'
            JOIN evaluations e ON e.candidate_id=c.id AND e.eligible=1
            WHERE e.id=(SELECT MAX(id) FROM evaluations WHERE candidate_id=c.id)
            ORDER BY e.score DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json"))
            item["reasons"] = json.loads(item.pop("reasons_json"))
            result.append(item)
        return result

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT c.*,s.alpha_id,s.checks_json,e.eligible,e.score,e.metrics_json
            FROM candidates c
            LEFT JOIN simulations s ON s.id=(
                SELECT id FROM simulations s2 WHERE s2.candidate_id=c.id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN evaluations e ON e.id=(
                SELECT id FROM evaluations e2 WHERE e2.candidate_id=c.id ORDER BY id DESC LIMIT 1
            )
            WHERE c.id=?
            """,
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None
