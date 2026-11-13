"""Validation layer: check every citation the models give against the warehouse.

Code only, no LLM. Never removes a patient; only adds flags (AGENTS.md).
The caller passes a read-only connection to data/warehouse.db.
"""

import re
import sqlite3

from src.models import Citation, Flag, FlagType, PatientResult

# Tables a model may cite, and each one's ID column.
# PatientContacts is deliberately missing, so it can never be cited.
ID_COLUMN = {
    "Patients": "LocalPatientIdentifier",
    "Inpatients": "EncounterID",
    "ECDS": "EncounterID",
    "Observations": "ObservationID",
    "Pathology": "PathologyID",
    "Diagnoses": "DiagnosisID",
    "Procedures": "ProcedureID",
    "ClinicalNotes": "NoteID",
}


def _norm(text) -> str:
    """Lower-case and collapse whitespace, so formatting differences don't count."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def check_citation(conn: sqlite3.Connection, c: Citation, patient_id: str) -> str | None:
    """Returns None if the citation is genuine, otherwise the reason it failed."""
    id_col = ID_COLUMN.get(c.table)
    if id_col is None:
        return "this table cannot be cited"

    row = conn.execute(
        f'SELECT * FROM "{c.table}" WHERE "{id_col}" = ?', (c.record_id,)
    ).fetchone()
    if row is None:
        return "record does not exist"
    if row["LocalPatientIdentifier"] != patient_id:
        return "record belongs to a different patient"
    if c.field not in row.keys() or row[c.field] is None:
        return "field is missing or empty"

    stored = row[c.field]
    if isinstance(stored, (int, float)):  # numbers: the quote must contain the stored value
        ok = any(float(n) == stored for n in re.findall(r"\d+(?:\.\d+)?", c.quote))
    else:  # text: the quote must appear in the stored text
        ok = len(_norm(c.quote)) >= 3 and _norm(c.quote) in _norm(stored)
    return None if ok else "quote does not match the record"


def validate_patient(conn: sqlite3.Connection, result: PatientResult) -> PatientResult:
    """Check every citation from both stages, and flag disagreements. Adds flags only."""
    for a in result.assessments:
        for stage, verdict in ((1, a.stage1), (2, a.stage2)):
            for c in verdict.citations if verdict else []:
                problem = check_citation(conn, c, result.patient_id)
                if problem:
                    a.flags.append(
                        Flag(
                            type=FlagType.citation_failed,
                            stage=stage,
                            detail=f"{c.table} {c.record_id}: {problem}",
                        )
                    )
        if a.stage1 and a.stage2 and a.stage1.verdict != a.stage2.verdict:
            a.flags.append(
                Flag(type=FlagType.stage_disagreement, detail="screener and verifier disagree")
            )
    return result
