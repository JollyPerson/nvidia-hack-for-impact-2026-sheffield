"""Validation layer: check every citation the models give against the warehouse.

Code only, no LLM. Never removes a patient; only adds flags (AGENTS.md).
The caller passes a read-only connection to data/warehouse.db.
"""

import re
import sqlite3

from src.models import Citation, Flag, FlagType, PatientResult, VerdictValue

# The list of citable tables (PatientContacts deliberately absent) lives in
# src/tools.py, shared with screening so the two can never disagree.
from src.tools import CITABLE_TABLES, resolve_table


def _norm(text) -> str:
    """Lower-case and collapse whitespace, so formatting differences don't count."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _clean_quote(quote: str) -> str:
    """Drop the '…' the tools add when they shorten a long note, plus stray quote marks."""
    return _norm(quote).strip(" .…\"'")


def check_citation(conn: sqlite3.Connection, c: Citation, patient_id: str) -> str | None:
    """Returns None if the citation is genuine, otherwise the reason it failed."""
    table = resolve_table(c.table)  # accepts "clinicalnotes", "ClinicalNote", ...
    if table is None:
        return "this table cannot be cited"

    row = conn.execute(
        f'SELECT * FROM "{table}" WHERE "{CITABLE_TABLES[table]}" = ?', (c.record_id,)
    ).fetchone()
    if row is None:
        return "record does not exist"
    if row["LocalPatientIdentifier"] != patient_id:
        return "record belongs to a different patient"

    field = next((k for k in row.keys() if k.lower() == c.field.strip().lower()), None)
    if field is None or row[field] is None:
        return "field is missing or empty"

    stored, quote = row[field], _clean_quote(c.quote)
    if isinstance(stored, (int, float)):  # numbers: the quote must contain the stored value
        ok = any(float(n) == stored for n in re.findall(r"\d+(?:\.\d+)?", quote))
    else:  # text: the quote must appear in the stored text
        ok = len(quote) >= 3 and quote in _norm(stored)
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
        # A real contradiction is met vs not_met. Stage 1 saying `insufficient` is not a
        # disagreement: AGENTS.md has it pass uncertain cases on for stage 2 to settle.
        if a.stage1 and a.stage2 and {a.stage1.verdict, a.stage2.verdict} == {
            VerdictValue.met,
            VerdictValue.not_met,
        }:
            a.flags.append(
                Flag(type=FlagType.stage_disagreement, detail="screener and verifier disagree")
            )
    return result
