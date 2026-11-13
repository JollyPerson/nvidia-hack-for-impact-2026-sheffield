"""Patient facts extracted ONCE per note at ingest, so a screening run reads them instead of calling a model.

Live screening (screening.py) asks a model about every (patient, criterion) pair on every run: the cost is
patients x criteria x runs. Here a model reads each note a single time, at ingest, and answers a fixed list
of questions (FACTS) with yes / no / uncertain / not_mentioned plus an exact quote. Code checks every quote
against the note before it is stored in PatientFacts (warehouse.db). A screening run then turns a notes
criterion that maps to a fact into a Verdict by code, in milliseconds, with the note quotes as citations.

    data/extract_facts.py   ingest: one model call per note -> PatientFacts rows (quotes checked)
    fact_verdict()          screening: PatientFacts -> Verdict for one patient and criterion, or None

Falls back to live screening (returns None) whenever the facts cannot answer safely:
- the criterion is not in the fact list (matched by its description, see FACTS[...].criteria);
- PatientFacts is missing, or any of the patient's notes has no extraction for that fact;
- any of the patient's notes had an extraction error or a quote that was not found in the note.

Code still decides (decide.py) and every fact citation still goes through validation.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

import config
from src.llm import NOTHING_FOUND
from src.models import Citation, Criterion, Verdict, VerdictValue

# Marks a verdict made by code from PatientFacts (no model call during the run).
FACTS_TAG = "[facts: extracted at ingest]"
VALUES = ("yes", "no", "uncertain", "not_mentioned")
# Stored instead of a value when the extraction cannot be trusted; such a note forces live screening.
ERROR, UNVERIFIED = "error", "unverified"


@dataclass(frozen=True)
class Fact:
    key: str
    question: str  # what the extraction model answers for each note
    criteria: tuple[str, ...] = field(default=())  # criterion descriptions (any study) this fact answers
    yes_needs: str | None = None  # regex a "yes" quote must contain (code guard), else not mentioned


FACTS: list[Fact] = [
    Fact("pneumonia_radiological",
         "Does the note record a clinical diagnosis of pneumonia CONFIRMED on imaging (e.g. consolidation on "
         "chest X-ray or CT)? 'uncertain' if imaging is equivocal, queried or awaited.",
         ("Clinical and radiological diagnosis of community-acquired pneumonia within the last 24 hours",
          "Pneumonia confirmed by consolidation on chest X-ray during this admission")),
    Fact("copd_home_oxygen",
         "Does the patient have COPD AND use long-term home oxygen (LTOT, home O2, oxygen concentrator)? "
         "'no' only if the note says they are not on home oxygen or have no COPD.",
         ("COPD on long-term home oxygen", "COPD requiring long-term home oxygen"),
         yes_needs=r"\bO2\b|oxygen|LTOT|concentrator"),
    Fact("immunosuppressed",
         "Is the patient immunosuppressed: current chemotherapy, high-dose steroids, other immunosuppressants, "
         "or an organ transplant? 'no' only if the note explicitly rules this out.",
         ("Immunosuppressed (chemotherapy, high-dose steroids or organ transplant)",)),
    Fact("lacks_capacity_or_eol",
         "Does the patient lack capacity to consent, or are they receiving end-of-life / palliative care? "
         "'no' only if the note says they have capacity / are not for palliation.",
         ("Lacks capacity to consent or is receiving end-of-life care",)),
    Fact("hf_iv_diuretics",
         "Was the patient admitted with new or worsening heart failure needing intravenous diuretics "
         "(e.g. IV furosemide)?",
         ("Admitted with new or worsening heart failure needing intravenous diuretics",)),
    Fact("acs_cause",
         "Is acute coronary syndrome (MI, NSTEMI, STEMI, unstable angina) the cause of this admission? "
         "'no' only if the note says ACS was ruled out (e.g. troponin negative, not ACS).",
         ("Acute coronary syndrome as the cause of this admission",)),
]
BY_KEY = {f.key: f for f in FACTS}


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


_BY_DESCRIPTION = {_norm(d): f for f in FACTS for d in f.criteria}


def fact_for(criterion: Criterion) -> Fact | None:
    """The fact that answers this notes criterion, matched on its description (case/space-insensitive)."""
    return _BY_DESCRIPTION.get(_norm(criterion.description))


def enabled() -> bool:
    return config.FACTS_ENABLED


# --------------------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS PatientFacts (
    NoteID                  TEXT NOT NULL,
    LocalPatientIdentifier  TEXT NOT NULL,
    FactKey                 TEXT NOT NULL,
    Value                   TEXT NOT NULL,   -- yes | no | uncertain | not_mentioned | error | unverified
    Quote                   TEXT,            -- exact text from the note (checked by code at ingest)
    Model                   TEXT,
    ExtractedAt             TEXT NOT NULL,
    PRIMARY KEY (NoteID, FactKey)
);
CREATE INDEX IF NOT EXISTS idx_facts_patient ON PatientFacts(LocalPatientIdentifier, FactKey);
"""


def init_table(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def has_table(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='PatientFacts'").fetchone() is not None


# --------------------------------------------------------------------------- extraction (ingest)

EXTRACT_SYSTEM = """You read ONE clinical note and answer a fixed list of questions about the patient.
For each question answer with:
- "yes": the note directly states it is true for this patient;
- "no": the note EXPLICITLY negates it (e.g. "no COPD", "not on home oxygen", "CXR clear", "PMH: nil",
  "troponin negative"). The quote must contain that negation. A note that lists other conditions, or
  says "Alert", or gives normal observations, does NOT say no: answer "not_mentioned";
- "uncertain": the note raises it but is equivocal ("?", "query", "possible", "awaited");
- "not_mentioned": the note does not address it. A note that does not mention a topic is NOT a "no".
For yes, no and uncertain, "quote" MUST be the exact words copied from the note that show it (a short
phrase, no paraphrase). For not_mentioned, "quote" is "".
Reply with ONE JSON object and nothing else."""


def extraction_schema(facts: list[Fact] = FACTS) -> dict:
    answer = {"type": "object",
              "properties": {"value": {"type": "string", "enum": list(VALUES)}, "quote": {"type": "string"}},
              "required": ["value", "quote"]}
    return {"type": "object", "properties": {f.key: answer for f in facts}, "required": [f.key for f in facts]}


def extraction_messages(note_text: str, facts: list[Fact] = FACTS) -> list[dict]:
    questions = "\n".join(f'- "{f.key}": {f.question}' for f in facts)
    shape = "{" + ", ".join(f'"{f.key}": {{"value": "...", "quote": "..."}}' for f in facts) + "}"
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"Questions:\n{questions}\n\nReply in this shape: {shape}\n\n"
                                    f"Clinical note:\n{note_text}"},
    ]


# A "no" is kept only when its quote itself negates something; otherwise the note just doesn't mention it.
NEGATION = re.compile(r"\b(no|not|nil|none|never|denies|denied|negative|neg|clear|without|absent|"
                      r"ruled out|r/o|excluded|unremarkable|normal)\b|\bn/?a\b", re.I)


def _quote_supports(f: Fact, value: str, quote: str) -> bool:
    """A "no" must quote an explicit negation; a "yes" must quote the fact's key term where it has one
    (Nemotron answered "COPD on home oxygen: yes" quoting only "PMH: COPD")."""
    if value == "no":
        return bool(NEGATION.search(quote))
    if value == "yes" and f.yes_needs:
        return bool(re.search(f.yes_needs, quote, re.I))
    return True


def parse_extraction(text: str | None, note_text: str, facts: list[Fact] = FACTS) -> dict[str, tuple[str, str]]:
    """Model reply -> {fact_key: (value, quote)}. Code checks every quote is really in the note:
    a yes / no / uncertain whose quote is missing or not found is stored as UNVERIFIED (live fallback)."""
    try:
        m = re.search(r"\{.*\}", text or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    note = _norm(note_text)
    out = {}
    for f in facts:
        item = data.get(f.key) if isinstance(data, dict) else None
        if not isinstance(item, dict) or item.get("value") not in VALUES:
            out[f.key] = (ERROR, "")
            continue
        value, quote = item["value"], str(item.get("quote") or "").strip().strip('"')
        if value == "not_mentioned":
            out[f.key] = (value, "")
        elif quote and _norm(quote) in note:
            if not _quote_supports(f, value, quote):
                value, quote = "not_mentioned", ""  # code rules: see _quote_supports
            out[f.key] = (value, quote)
        else:
            out[f.key] = (UNVERIFIED, quote)
    return out


def extract_note(llm, note_text: str, facts: list[Fact] = FACTS) -> dict[str, tuple[str, str]]:
    """One model call (the screener model, reasoning as configured) for one note. Retries once on bad JSON."""
    fmt = {"type": "json_schema", "json_schema": {"name": "patient_facts", "schema": extraction_schema(facts)}}
    messages = extraction_messages(note_text, facts)
    parsed: dict[str, tuple[str, str]] = {}
    for _attempt in (1, 2):
        resp = llm._create("screener", messages, response_format=fmt)
        parsed = parse_extraction(resp.choices[0].message.content, note_text, facts)
        if not any(v == ERROR for v, _ in parsed.values()):
            return parsed
    return parsed


# --------------------------------------------------------------------------- screening (code, no model)


def fact_verdict(conn, patient_id: str, criterion: Criterion) -> Verdict | None:
    """A Verdict for this patient and criterion made by code from PatientFacts, or None to screen live.

    Every note of the patient must have a trusted extraction for the fact. Then:
    any yes and no no -> met; any no and no yes -> not_met; yes and no together, or any uncertain ->
    insufficient (with the quotes, so a clinician reviews); nothing in any note -> insufficient marked as
    "all notes read, nothing found" (decide.py may count an exclusion as not mentioned)."""
    from src.screening import ALL_NOTES_READ  # screening imports this module

    fact = fact_for(criterion) if enabled() else None
    if fact is None or not has_table(conn):
        return None
    notes = [r[0] for r in conn.execute(
        "SELECT NoteID FROM ClinicalNotes WHERE LocalPatientIdentifier = ?", (patient_id,)).fetchall()]
    rows = conn.execute(
        "SELECT NoteID, Value, Quote FROM PatientFacts WHERE LocalPatientIdentifier = ? AND FactKey = ?",
        (patient_id, fact.key)).fetchall()
    by_note = {r[0]: (r[1], r[2]) if _quote_supports(fact, r[1], r[2] or "") else ("not_mentioned", "")
               for r in rows}
    if any(n not in by_note or by_note[n][0] not in VALUES for n in notes):
        return None  # a note not extracted yet, an extraction error or an unverified quote: screen live

    def cites(value):
        return [Citation(table="ClinicalNotes", record_id=n, field="NoteText", quote=q)
                for n, (v, q) in sorted(by_note.items()) if v == value and n in notes]

    yes, no, unsure = cites("yes"), cites("no"), cites("uncertain")
    n = len(notes)
    if yes and not no and not unsure:
        return Verdict(verdict=VerdictValue.met, citations=yes,
                       reasoning=f"{FACTS_TAG} {len(yes)} of {n} notes state it.")
    if no and not yes and not unsure:
        return Verdict(verdict=VerdictValue.not_met, citations=no,
                       reasoning=f"{FACTS_TAG} {len(no)} of {n} notes state it is not the case.")
    if yes or no or unsure:
        return Verdict(verdict=VerdictValue.insufficient, citations=yes + no + unsure,
                       reasoning=f"{FACTS_TAG} The notes conflict or are uncertain "
                                 f"({len(yes)} yes, {len(no)} no, {len(unsure)} uncertain).")
    return Verdict(verdict=VerdictValue.insufficient, citations=[],
                   reasoning=f"{ALL_NOTES_READ} {NOTHING_FOUND} {FACTS_TAG} None of the {n} notes mention it.")
