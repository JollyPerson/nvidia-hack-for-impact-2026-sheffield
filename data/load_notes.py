"""Load the LLM-written clinical notes in data/notes_cache.json into ClinicalNotes in warehouse.db.

Run from the project root, after data/generate.py:

    python data/load_notes.py

The cache holds 566 notes (triage, clerking, ward round) that Qwen wrote FROM each encounter's structured
record, but it is keyed by a hash of the generation prompt, so it does not say which encounter a note
belongs to. Each note copies that encounter's observations (for example "RR 27, SpO2 93% RA, HR 123,
BP 84/50") and the patient's age and sex ("77F"), so this script matches them back:

- a note matches an Observations row when heart rate, blood pressure, respiratory rate and SpO2 agree
  (at least 4 of the 5 points, with blood pressure worth 2), and the patient's age (within a year) and sex
  agree when the note states them;
- a note is loaded only when exactly ONE encounter matches. Ambiguous and unmatched notes are skipped
  (and counted), never guessed.

Checked on the seeded warehouse: every loaded ward-round note's "(dd/mm)" date falls on an observation
date of its encounter, and notes whose impression says pneumonia land on J18/J15/J69 encounters.

Deterministic: same warehouse + same cache -> same NoteIDs and rows. Safe to rerun (it replaces the
notes it loaded before). All data is synthetic.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
CACHE = DATA_DIR / "notes_cache.json"
DB_PATH = DATA_DIR / "warehouse.db"

AUTHOR = {"triage": "ED triage nurse", "clerking": "Medical FY2", "ward round": "Medical registrar"}
# Minutes after the matched observation that the note is written (triage is at the same time).
OFFSET_MINUTES = {"triage": 0, "clerking": 40, "ward round": 30}
MIN_SCORE = 4


def _first_ints(pattern: str, text: str) -> tuple[int, ...] | None:
    m = re.search(pattern, text)
    return tuple(int(g) for g in m.groups()) if m else None


def vitals(text: str) -> dict:
    """The first set of vitals a note states, plus age and sex when it gives them."""
    age_sex = re.search(r"\b(\d{1,3}) ?([MF])\b", text) or re.search(
        r"\b(\d{1,3})(?: ?yo| ?y/o| year old| years old) (male|female|man|woman|M|F)\b", text, re.I)
    return {
        "hr": _first_ints(r"\bHR:? (\d{2,3})", text),
        "bp": _first_ints(r"\bBP:? (\d{2,3})/(\d{2,3})", text),
        "rr": _first_ints(r"\bRR:? (\d{1,2})", text),
        "spo2": _first_ints(r"\bSpO2:? (\d{2,3})", text),
        "age": int(age_sex.group(1)) if age_sex else None,
        "sex": age_sex.group(2)[0].upper() if age_sex else None,
    }


def _age_on(dob: str, when: str) -> int:
    born, day = date.fromisoformat(dob[:10]), date.fromisoformat(when[:10])
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def match(conn: sqlite3.Connection, notes: dict[str, dict]) -> tuple[list[tuple], Counter]:
    """Returns (rows for ClinicalNotes, counts of what happened to each note)."""
    obs = conn.execute(
        "SELECT o.ObservationID, o.EncounterID, o.LocalPatientIdentifier, o.RecordedDateTime, o.HeartRate, "
        "o.SystolicBP, o.DiastolicBP, o.RespRate, o.SpO2, p.DateOfBirth, p.Sex "
        "FROM Observations o JOIN Patients p USING (LocalPatientIdentifier)"
    ).fetchall()
    by_hr = defaultdict(list)
    for o in obs:
        by_hr[o[4]].append(o)

    counts: Counter = Counter()
    matched = []
    for key in sorted(notes):
        note = notes[key]
        v = vitals(note["text"])
        if not v["hr"] or not v["bp"]:
            counts["no vitals in the note"] += 1
            continue
        best: dict[str, tuple[int, tuple]] = {}
        for o in by_hr.get(v["hr"][0], []):
            score = 1 + 2 * ((o[5], o[6]) == v["bp"]) + (v["rr"] is not None and o[7] == v["rr"][0]) \
                + (v["spo2"] is not None and o[8] == v["spo2"][0])
            if score < MIN_SCORE:
                continue
            if v["age"] is not None and (abs(_age_on(o[9], o[3]) - v["age"]) > 1 or o[10][0].upper() != v["sex"]):
                continue
            enc = o[1]
            if enc not in best or (score, o[3]) > (best[enc][0], best[enc][1][3]):
                best[enc] = (score, o)
        if len(best) != 1:
            counts["no match" if not best else "ambiguous"] += 1
            continue
        (_, o), = best.values()
        when = datetime.fromisoformat(o[3]) + timedelta(minutes=OFFSET_MINUTES.get(note["note_type"], 0))
        matched.append((o[1], o[2], note["note_type"], when.isoformat(timespec="seconds"), note["text"]))
        counts["loaded"] += 1

    matched.sort(key=lambda r: (r[0], r[3], r[2]))
    rows = [(f"NOTE-{i:06d}", enc, pid, ntype, AUTHOR.get(ntype, "Clinician"), when, text)
            for i, (enc, pid, ntype, when, text) in enumerate(matched, 1)]
    return rows, counts


def load(db_path: Path | str = DB_PATH, cache: Path | str = CACHE) -> Counter:
    notes = json.loads(Path(cache).read_text())
    conn = sqlite3.connect(str(db_path))
    try:
        rows, counts = match(conn, notes)
        with conn:
            conn.execute("DELETE FROM ClinicalNotes WHERE NoteID LIKE 'NOTE-%'")
            conn.executemany("INSERT INTO ClinicalNotes VALUES (?,?,?,?,?,?,?)", rows)
        counts["encounters with notes"] = len({r[1] for r in rows})
    finally:
        conn.close()
    return counts


def main() -> None:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    if not db.exists():
        sys.exit(f"{db} not found; build it first with: python data/generate.py")
    counts = load(db)
    print(f"{db}: ClinicalNotes from {CACHE.name}: " + ", ".join(f"{k} {n}" for k, n in counts.items()))


if __name__ == "__main__":
    main()
