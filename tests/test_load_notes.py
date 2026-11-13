"""Tests for data/load_notes.py: cached notes are matched to encounters by the vitals and age/sex they quote."""

import sqlite3
from pathlib import Path

from data.load_notes import match, vitals

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    conn.executescript("""
        INSERT INTO Patients VALUES ('MRN1', '1949-01-01', 'Female', 'A', 'S1');
        INSERT INTO Patients VALUES ('MRN2', '1980-01-01', 'Male', 'A', 'S2');
        INSERT INTO Observations (ObservationID, EncounterID, LocalPatientIdentifier, RecordedDateTime, RespRate, SpO2,
            OnOxygen, Temperature, SystolicBP, DiastolicBP, HeartRate, Consciousness)
            VALUES ('O1', 'IP-1', 'MRN1', '2026-11-12T10:00:00', 27, 93, 0, 38.8, 84, 50, 123, 'C'),
                   ('O2', 'IP-2', 'MRN2', '2026-11-12T11:00:00', 27, 93, 0, 38.8, 84, 50, 123, 'A'),
                   ('O3', 'IP-3', 'MRN2', '2026-11-11T11:00:00', 18, 97, 0, 37.0, 120, 80, 88, 'A'),
                   ('O4', 'IP-4', 'MRN2', '2026-11-10T11:00:00', 18, 97, 0, 37.0, 120, 80, 88, 'A');
    """)
    return conn


def note(text, kind="clerking"):
    return {"model": "m", "note_type": kind, "text": text}


def test_vitals_parsing():
    v = vitals("HPC: 77F presents. Obs on arrival: RR 27, SpO2 93% RA, HR 123, BP 84/50, T 38.8.")
    assert (v["hr"], v["bp"], v["rr"], v["spo2"], v["age"], v["sex"]) == ((123,), (84, 50), (27,), (93,), 77, "F")


def test_matches_one_encounter_using_age_and_sex():
    rows, counts = match(db(), {"a": note("77F. RR 27, SpO2 93% RA, HR 123, BP 84/50.")})
    assert counts["loaded"] == 1
    note_id, enc, pid, kind, author, when, text = rows[0]
    assert (note_id, enc, pid, kind) == ("NOTE-000001", "IP-1", "MRN1", "clerking")
    assert when == "2026-11-12T10:40:00" and author


def test_ambiguous_and_unmatched_notes_are_skipped_not_guessed():
    notes = {
        "same vitals twice": note("46M. RR 18, SpO2 97%, HR 88, BP 120/80."),
        "nothing like it": note("RR 12, SpO2 99%, HR 60, BP 100/60."),
        "no vitals": note("Pt comfortable overnight."),
    }
    rows, counts = match(db(), notes)
    assert rows == []
    assert (counts["ambiguous"], counts["no match"], counts["no vitals in the note"]) == (1, 1, 1)
