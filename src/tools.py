"""Tools the screening agent calls to fetch evidence itself (see AGENTS.md).

Owns: search_notes, get_labs, get_observations, get_diagnoses and their schemas.

Every tool is bound by code to ONE patient: the model never chooses a patient
ID, so it cannot read anyone else's records. PatientContacts is never read here.
Results are compact JSON, cut to fit the 8k token limit. Each row carries its
table's primary key so the model can cite it.
"""

from __future__ import annotations

import json
import re
import sqlite3

import config

# OpenAI-style tool schemas sent to the model.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search this patient's free-text clinical notes by keywords. "
            "Returns the best-matching notes (NoteID, type, date, text). "
            "Use an empty query to get the most recent notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords, e.g. 'home oxygen COPD'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_labs",
            "description": "Get this patient's pathology results, newest first. "
            "Optionally filter by test name or code, e.g. 'CRP' or 'eGFR'.",
            "parameters": {
                "type": "object",
                "properties": {"test": {"type": "string", "description": "Test name or code (optional)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_observations",
            "description": "Get this patient's most recent vital-sign observations "
            "(resp rate, SpO2, on oxygen, temperature, BP, heart rate, consciousness).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diagnoses",
            "description": "Get this patient's coded diagnoses (ICD-10 code, description, primary/secondary).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}

# Tables a citation or stage-1 pointer may refer to, with their primary key.
# PatientContacts is deliberately absent.
CITABLE_TABLES = {
    "Patients": "LocalPatientIdentifier",
    "Inpatients": "EncounterID",
    "ECDS": "EncounterID",
    "Observations": "ObservationID",
    "Pathology": "PathologyID",
    "Diagnoses": "DiagnosisID",
    "Procedures": "ProcedureID",
    "ClinicalNotes": "NoteID",
}

_STOPWORDS = {"the", "and", "for", "with", "not", "has", "was", "are", "any", "patient", "history"}


def connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _cut(text: str, terms: list[str], limit: int) -> str:
    """Cut long text to `limit` chars, keeping a window around the first match.
    The kept part is copied verbatim so the model can quote it exactly."""
    if len(text) <= limit:
        return text
    low = text.lower()
    hits = [low.find(t) for t in terms if low.find(t) != -1]
    start = max(0, min(hits) - limit // 3) if hits else 0
    start = min(start, len(text) - limit)
    piece = text[start : start + limit]
    return ("…" if start > 0 else "") + piece + ("…" if start + limit < len(text) else "")


class PatientTools:
    """The agent's tools, bound to one patient."""

    def __init__(self, conn: sqlite3.Connection, patient_id: str):
        self.conn = conn
        self.patient_id = patient_id

    # -- dispatcher ----------------------------------------------------------------

    def run(self, name: str, arguments: str | dict | None) -> str:
        """Run a tool call from the model. Always returns a string (errors included)."""
        if name not in TOOL_NAMES:
            return _dumps({"error": f"unknown tool '{name}'. Available: {sorted(TOOL_NAMES)}"})
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            return _dumps({"error": f"bad arguments: {e}"})
        try:
            if name == "search_notes":
                return self.search_notes(str(args.get("query") or ""))
            if name == "get_labs":
                return self.get_labs(args.get("test") or None)
            if name == "get_observations":
                return self.get_observations()
            return self.get_diagnoses()
        except sqlite3.Error as e:
            return _dumps({"error": f"database error: {e}"})

    # -- tools -----------------------------------------------------------------------

    def search_notes(self, query: str) -> str:
        terms = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 2 and w not in _STOPWORDS]
        rows = self.conn.execute(
            "SELECT NoteID, NoteType, CreatedDateTime, NoteText FROM ClinicalNotes "
            "WHERE LocalPatientIdentifier = ? ORDER BY CreatedDateTime DESC",
            (self.patient_id,),
        ).fetchall()
        if terms:
            scored = [(sum(t in r["NoteText"].lower() for t in terms), r) for r in rows]
            matched = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s > 0]  # stable: newest first on ties
        else:
            matched = list(rows)
        notes = [
            {
                "NoteID": r["NoteID"],
                "NoteType": r["NoteType"],
                "CreatedDateTime": r["CreatedDateTime"],
                "NoteText": _cut(r["NoteText"], terms, config.MAX_NOTE_CHARS),
            }
            for r in matched[: config.MAX_NOTES_RETURNED]
        ]
        result = {"table": "ClinicalNotes", "notes": notes, "total_notes_for_patient": len(rows)}
        if not notes:
            result["message"] = "No notes match. Try other keywords or an empty query."
        return _dumps(result)

    def get_labs(self, test: str | None = None) -> str:
        sql = (
            "SELECT PathologyID, TestCode, TestName, Value, Units, RefLow, RefHigh, CollectedDateTime "
            "FROM Pathology WHERE LocalPatientIdentifier = ?"
        )
        params: list = [self.patient_id]
        if test:
            sql += " AND (TestCode LIKE ? OR TestName LIKE ?)"
            params += [f"%{test}%", f"%{test}%"]
        sql += " ORDER BY CollectedDateTime DESC LIMIT ?"
        params.append(config.MAX_ROWS_RETURNED)
        return self._rows("Pathology", sql, params)

    def get_observations(self) -> str:
        sql = (
            "SELECT ObservationID, RecordedDateTime, RespRate, SpO2, OnOxygen, Temperature, "
            "SystolicBP, DiastolicBP, HeartRate, Consciousness FROM Observations "
            "WHERE LocalPatientIdentifier = ? ORDER BY RecordedDateTime DESC LIMIT ?"
        )
        return self._rows("Observations", sql, [self.patient_id, config.MAX_ROWS_RETURNED])

    def get_diagnoses(self) -> str:
        sql = (
            "SELECT DiagnosisID, ICD10Code, Description, Position FROM Diagnoses "
            "WHERE LocalPatientIdentifier = ? ORDER BY Position, DiagnosisID LIMIT ?"
        )
        return self._rows("Diagnoses", sql, [self.patient_id, config.MAX_ROWS_RETURNED])

    def _rows(self, table: str, sql: str, params: list) -> str:
        rows = [{k: v for k, v in dict(r).items() if v is not None} for r in self.conn.execute(sql, params)]
        result = {"table": table, "rows": rows}
        if not rows:
            result["message"] = f"No {table} records for this patient."
        return _dumps(result)

    # -- code-only helpers (not offered to the model) ----------------------------------

    def patient_summary(self) -> dict:
        """Age-relevant basics only. Never contact details."""
        r = self.conn.execute(
            "SELECT DateOfBirth, Sex FROM Patients WHERE LocalPatientIdentifier = ?", (self.patient_id,)
        ).fetchone()
        return dict(r) if r else {}

    def get_record(self, table: str, record_id: str) -> dict | None:
        """Fetch one cited record, only if it belongs to this patient.
        Used to hand stage 2 the ORIGINAL records stage 1 pointed at."""
        key = CITABLE_TABLES.get(table)
        if key is None:
            return None
        r = self.conn.execute(
            f"SELECT * FROM {table} WHERE {key} = ? AND LocalPatientIdentifier = ?",
            (record_id, self.patient_id),
        ).fetchone()
        if r is None:
            return None
        rec = {k: v for k, v in dict(r).items() if v is not None}
        if "NoteText" in rec:
            rec["NoteText"] = _cut(rec["NoteText"], [], config.MAX_NOTE_CHARS)
        return {"table": table, **rec}
