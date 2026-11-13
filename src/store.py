"""Reads and writes the application database (data/app.db).

Owns: studies and their criteria, screening runs and their patient results,
clinician decisions, and the audit log. Every function returns src.models
objects, so the API and the pages see the same shapes as the pipeline.

Never stores patient details (age, sex, names, contacts): only the
LocalPatientIdentifier. Details stay in the warehouse and are read when a page
is displayed.

Every function takes an optional `db` path, so tests can use a temporary file.

    from src import store
    store.init_db()
    study = store.create_study(Study(...))
    store.save_run(ScreeningRun(...))
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import TypeAdapter

from src.models import (
    AuditEntry,
    Criterion,
    CriterionAssessment,
    Decision,
    PatientResult,
    ScreeningRun,
    Study,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "data" / "app_schema.sql"
# Settings belong in config.py (AGENTS.md); move this there when convenient.
APP_DB_PATH = Path(os.getenv("APP_DB_PATH", ROOT / "data" / "app.db"))

_ASSESSMENTS = TypeAdapter(list[CriterionAssessment])

STUDY_COLUMNS = [
    "id", "title", "short_title", "condition", "study_design", "phase", "intervention",
    "primary_outcome", "target_sample_size", "recruitment_start", "recruitment_end",
    "chief_investigator", "sponsor", "ethics_reference", "status",
]
CRITERION_COLUMNS = ["id", "type", "description", "kind", "field", "operator", "value", "time_window_days"]
RUN_COLUMNS = [
    "run_id", "study_id", "status", "started_at", "finished_at",
    "candidates_after_sql", "shortlisted_stage1", "confirmed_stage2",
    "likely_eligible", "needs_review", "excluded",
]


@contextmanager
def _connect(db: Path | str | None) -> Iterator[sqlite3.Connection]:
    """One connection per call: commits on success, rolls back on error."""
    conn = sqlite3.connect(db or APP_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # transaction
            yield conn
    finally:
        conn.close()


def init_db(db: Path | str | None = None) -> None:
    """Create the tables if they do not exist. Safe to call more than once."""
    with _connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


def list_studies(db: Path | str | None = None) -> list[Study]:
    """All studies, each with its criteria, in id order."""
    with _connect(db) as conn:
        rows = conn.execute("SELECT * FROM Studies ORDER BY id").fetchall()
        return [_study_from_row(conn, row) for row in rows]


def get_study(study_id: str, db: Path | str | None = None) -> Study | None:
    with _connect(db) as conn:
        row = conn.execute("SELECT * FROM Studies WHERE id = ?", (study_id,)).fetchone()
        return _study_from_row(conn, row) if row else None


def create_study(study: Study, db: Path | str | None = None) -> Study:
    """Save a new study and its criteria. Returns it as stored, with ids filled in.

    Without an id the study gets the next "S-NNN"; criteria without ids get
    "C1", "C2", ... in the order given.
    """
    with _connect(db) as conn:
        study_id = study.id or _next_study_id(conn)
        data = study.model_dump(mode="json", exclude={"criteria"})
        data["id"] = study_id
        conn.execute(
            f"INSERT INTO Studies ({', '.join(STUDY_COLUMNS)}) VALUES ({', '.join('?' * len(STUDY_COLUMNS))})",
            [data[c] for c in STUDY_COLUMNS],
        )
        used = {c.id for c in study.criteria if c.id}
        number = 0
        for position, criterion in enumerate(study.criteria):
            crit = criterion.model_dump(mode="json")
            if not crit["id"]:
                number += 1
                while f"C{number}" in used:
                    number += 1
                crit["id"] = f"C{number}"
            conn.execute(
                "INSERT INTO Criteria (study_id, position, "
                f"{', '.join(CRITERION_COLUMNS)}) VALUES (?, ?, {', '.join('?' * len(CRITERION_COLUMNS))})",
                [study_id, position] + [crit[c] for c in CRITERION_COLUMNS],
            )
        row = conn.execute("SELECT * FROM Studies WHERE id = ?", (study_id,)).fetchone()
        return _study_from_row(conn, row)


def _next_study_id(conn: sqlite3.Connection) -> str:
    numbers = [int(m.group(1)) for (sid,) in conn.execute("SELECT id FROM Studies")
               if (m := re.fullmatch(r"S-(\d+)", sid))]
    return f"S-{max(numbers, default=0) + 1:03d}"


def _study_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Study:
    criteria = [
        Criterion(**{c: crit[c] for c in CRITERION_COLUMNS})
        for crit in conn.execute(
            "SELECT * FROM Criteria WHERE study_id = ? ORDER BY position", (row["id"],)
        )
    ]
    return Study(**{c: row[c] for c in STUDY_COLUMNS}, criteria=criteria)


# ---------------------------------------------------------------------------
# Screening runs
# ---------------------------------------------------------------------------


def save_run(run: ScreeningRun, db: Path | str | None = None) -> ScreeningRun:
    """Insert or update a run and its patient results. Call again as the run progresses.

    A patient's `approved` always follows their latest recorded decision, so
    re-saving results from the pipeline never undoes a clinician's approval.
    """
    with _connect(db) as conn:
        data = run.model_dump(mode="json", exclude={"patients"})
        updates = ", ".join(f"{c} = excluded.{c}" for c in RUN_COLUMNS[1:])
        conn.execute(
            f"INSERT INTO ScreeningRuns ({', '.join(RUN_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(RUN_COLUMNS))}) "
            f"ON CONFLICT (run_id) DO UPDATE SET {updates}",
            [data[c] for c in RUN_COLUMNS],
        )
        decided = _latest_decisions(conn, run.run_id)
        for result in run.patients:
            approved = result.approved
            if result.patient_id in decided:
                approved = decided[result.patient_id].decision == "approved"
            conn.execute(
                """INSERT INTO PatientResults
                       (run_id, study_id, patient_id, shortlisted, assessments, status, reasons, approved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (run_id, patient_id) DO UPDATE SET
                       study_id = excluded.study_id, shortlisted = excluded.shortlisted,
                       assessments = excluded.assessments, status = excluded.status,
                       reasons = excluded.reasons, approved = excluded.approved""",
                (
                    run.run_id,
                    result.study_id,
                    result.patient_id,
                    int(result.shortlisted),
                    _ASSESSMENTS.dump_json(result.assessments).decode(),
                    result.status.value,
                    json.dumps(result.reasons),
                    int(approved),
                ),
            )
        return _run_from_row(conn, conn.execute("SELECT * FROM ScreeningRuns WHERE run_id = ?", (run.run_id,)).fetchone())


def get_run(run_id: str, db: Path | str | None = None) -> ScreeningRun | None:
    with _connect(db) as conn:
        row = conn.execute("SELECT * FROM ScreeningRuns WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(conn, row) if row else None


def get_latest_run(study_id: str, db: Path | str | None = None) -> ScreeningRun | None:
    """The most recently started run for a study, for the study page."""
    with _connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM ScreeningRuns WHERE study_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (study_id,),
        ).fetchone()
        return _run_from_row(conn, row) if row else None


def _run_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> ScreeningRun:
    patients = [
        PatientResult(
            study_id=r["study_id"],
            patient_id=r["patient_id"],
            shortlisted=bool(r["shortlisted"]),
            assessments=_ASSESSMENTS.validate_json(r["assessments"]),
            status=r["status"],
            reasons=json.loads(r["reasons"]),
            approved=bool(r["approved"]),
        )
        for r in conn.execute("SELECT * FROM PatientResults WHERE run_id = ? ORDER BY rowid", (row["run_id"],))
    ]
    return ScreeningRun(**{c: row[c] for c in RUN_COLUMNS}, patients=patients)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def record_decision(decision: Decision, db: Path | str | None = None) -> Decision:
    """Store a clinician's decision and keep the patient's `approved` flag in step.

    History is kept: earlier decisions stay in the table, the latest one counts.
    The patient must be in the run, otherwise sqlite3.IntegrityError is raised.
    """
    with _connect(db) as conn:
        data = decision.model_dump(mode="json")
        conn.execute(
            'INSERT INTO Decisions (patient_id, run_id, decision, note, who, "when") VALUES (?, ?, ?, ?, ?, ?)',
            (data["patient_id"], data["run_id"], data["decision"], data["note"], data["who"], data["when"]),
        )
        conn.execute(
            "UPDATE PatientResults SET approved = ? WHERE run_id = ? AND patient_id = ?",
            (int(decision.decision == "approved"), decision.run_id, decision.patient_id),
        )
        return decision


def get_decisions(run_id: str, db: Path | str | None = None) -> dict[str, Decision]:
    """The current (latest) decision for each patient in a run, keyed by patient_id."""
    with _connect(db) as conn:
        return _latest_decisions(conn, run_id)


def _latest_decisions(conn: sqlite3.Connection, run_id: str) -> dict[str, Decision]:
    latest: dict[str, Decision] = {}
    for r in conn.execute('SELECT * FROM Decisions WHERE run_id = ? ORDER BY id', (run_id,)):
        latest[r["patient_id"]] = Decision(
            patient_id=r["patient_id"], run_id=r["run_id"], decision=r["decision"],
            note=r["note"], who=r["who"], when=r["when"],
        )
    return latest


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def append_audit(entry: AuditEntry, db: Path | str | None = None) -> AuditEntry:
    with _connect(db) as conn:
        data = entry.model_dump(mode="json")
        conn.execute(
            'INSERT INTO AuditLog ("when", study_id, who, action, patient_id, note) VALUES (?, ?, ?, ?, ?, ?)',
            (data["when"], data["study_id"], data["who"], data["action"], data["patient_id"], data["note"]),
        )
        return entry


def read_audit(study_id: str | None = None, db: Path | str | None = None) -> list[AuditEntry]:
    """Audit entries in the order they were written, for one study or all."""
    sql = 'SELECT "when", study_id, who, action, patient_id, note FROM AuditLog'
    params: tuple = ()
    if study_id is not None:
        sql += " WHERE study_id = ?"
        params = (study_id,)
    with _connect(db) as conn:
        return [AuditEntry(**dict(r)) for r in conn.execute(sql + " ORDER BY id", params)]
