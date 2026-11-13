"""Validation layer: check every citation against the warehouse.

Code only, no LLM. Never removes a patient; only adds flags (AGENTS.md).
Checks citations from the two models and from the prefilter alike.
The caller passes a read-only connection to data/warehouse.db.
"""

import math
import re
import sqlite3
from datetime import date, datetime

from src.models import Citation, Flag, FlagType, PatientResult, VerdictValue
from src.scores import NEW_CONFUSION, compute_curb65, compute_news2

# The list of citable tables (PatientContacts deliberately absent) lives in
# src/tools.py, shared with screening so the two can never disagree.
from src.tools import CITABLE_TABLES, resolve_table

# Scores the prefilter cites on an Observations row. They are not stored, so
# validation recalculates them with src/scores.py (see src/prefilter.py).
SCORES = {"CURB65", "NEWS2"}


def _norm(text) -> str:
    """Lower-case and collapse whitespace, so formatting differences don't count."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _clean_quote(quote: str) -> str:
    """Drop the '…' the tools add when they shorten a long note, plus stray quote marks.
    Notes reach the models as JSON, so a copied line break may arrive as the two characters \\n."""
    quote = quote.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
    return _norm(quote).strip(" .…\"'")


def _quote_parts(quote: str) -> list[str]:
    """Split a quote where the model left words out ("Working dx UTI... Impression: sepsis")."""
    return [p for p in (_clean_quote(x) for x in re.split(r"\.{2,}|…", quote)) if p]


def _quote_has_number(quote: str, value: float) -> bool:
    """True if a number in the quote equals the value (7.2 == 7.20; tiny rounding allowed)."""
    return any(
        math.isclose(float(n), float(value), rel_tol=1e-5, abs_tol=1e-9)
        for n in re.findall(r"\d+(?:\.\d+)?", quote)
    )


def _age_on(dob: str, day: date) -> int:
    born = date.fromisoformat(dob)
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def _recalculate_score(conn, name: str, obs: sqlite3.Row, patient_id: str, siblings) -> int | None:
    """Recalculate CURB-65 or NEWS2 from the warehouse, exactly as the prefilter does.
    Returns None if the records needed are missing."""
    try:
        if name == "NEWS2":
            return compute_news2(
                resp_rate=obs["RespRate"], spo2=obs["SpO2"], on_oxygen=bool(obs["OnOxygen"]),
                temperature=obs["Temperature"], systolic_bp=obs["SystolicBP"],
                heart_rate=obs["HeartRate"], consciousness=obs["Consciousness"],
            )
        # CURB-65 also needs urea (the urea result cited next to it) and age (on the obs date).
        urea = None
        for s in siblings:
            if resolve_table(s.table) == "Pathology":
                urea = conn.execute(
                    "SELECT Value FROM Pathology WHERE PathologyID = ? AND LocalPatientIdentifier = ? "
                    "AND TestCode = 'UREA'",
                    (s.record_id, patient_id),
                ).fetchone() or urea
        dob = conn.execute(
            "SELECT DateOfBirth FROM Patients WHERE LocalPatientIdentifier = ?", (patient_id,)
        ).fetchone()
        if urea is None or dob is None:
            return None
        return compute_curb65(
            confusion=obs["Consciousness"] in NEW_CONFUSION, urea=urea["Value"],
            resp_rate=obs["RespRate"], systolic_bp=obs["SystolicBP"], diastolic_bp=obs["DiastolicBP"],
            age=_age_on(dob["DateOfBirth"], datetime.fromisoformat(obs["RecordedDateTime"]).date()),
        )
    except (TypeError, ValueError):  # a missing (NULL) vital sign or a bad date
        return None


def check_citation(
    conn: sqlite3.Connection, c: Citation, patient_id: str, siblings: list[Citation] = ()
) -> str | None:
    """Returns None if the citation is genuine, otherwise the reason it failed.
    `siblings` are the other citations given with it (CURB-65 needs the cited urea)."""
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

    quote = _clean_quote(c.quote)
    score_name = c.field.strip().upper()
    if table == "Observations" and score_name in SCORES:
        score = _recalculate_score(conn, score_name, row, patient_id, siblings)
        if score is None:
            return "score cannot be recalculated from the records"
        return None if _quote_has_number(quote, score) else "quote does not match the recalculated score"

    field = next((k for k in row.keys() if k.lower() == c.field.strip().lower()), None)
    if field is None:
        return "field is missing or empty"
    stored = row[field]
    if stored is None:  # an empty quote may stand for an empty field (e.g. not yet discharged)
        return None if quote == "" else "field is missing or empty"

    if isinstance(stored, (int, float)):  # numbers: the quote must contain the stored value
        ok = _quote_has_number(quote, stored)
    else:  # text: the exact value (e.g. a short code), or passages of at least 3 characters.
        # A quote with "..." passes only if EVERY part really appears in this same record.
        text, parts = _norm(stored), _quote_parts(c.quote)
        ok = quote == text or (bool(parts) and all(len(p) >= 3 and p in text for p in parts))
    return None if ok else "quote does not match the record"


def check_citations(conn: sqlite3.Connection, citations: list[Citation], patient_id: str) -> list[str]:
    """Check a set of citations given together (one verdict, or one prefilter criterion).
    Returns one line per failed citation; an empty list means all are genuine."""
    problems = []
    for c in citations:
        problem = check_citation(conn, c, patient_id, siblings=citations)
        if problem:
            problems.append(f"{c.table} {c.record_id}: {problem}")
    return problems


def validate_patient(conn: sqlite3.Connection, result: PatientResult) -> PatientResult:
    """Check every citation from both stages, and flag disagreements. Adds flags only."""
    for a in result.assessments:
        for stage, verdict in ((1, a.stage1), (2, a.stage2)):
            for problem in check_citations(conn, verdict.citations, result.patient_id) if verdict else []:
                a.flags.append(Flag(type=FlagType.citation_failed, stage=stage, detail=problem))
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
