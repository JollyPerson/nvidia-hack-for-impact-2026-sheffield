"""Five hand-built test patients with known answers (organiser's 5-input test).

Used by tests/test_screening.py (with a fake model) and by
scripts/smoke_test_models.py (with the real models on the Spark).

Study criteria (both `notes` kind, so they go to the LLMs):
  C1 inclusion: pneumonia confirmed by consolidation on chest X-ray
  C2 exclusion: COPD needing long-term home oxygen

  P001  clear match              C1 met,          C2 not_met
  P002  clear exclusion          C2 met  -> dropped at stage 1
  P003  ambiguous X-ray          C1 insufficient  ("?consolidation vs atelectasis")
  P004  negation trap            C1 met,          C2 not_met ("COPD ... NOT on home O2")
  P005  nothing in the records   C1 insufficient, C2 insufficient ("I don't know" is right)
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from src.models import Criterion, Study

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"

STUDY = Study(
    id="S-TEST",
    title="Test study: community-acquired pneumonia",
    condition="Community-acquired pneumonia",
    study_design="observational",
    intervention="none",
    primary_outcome="none",
    target_sample_size=5,
    recruitment_start=date(2026, 11, 1),
    recruitment_end=date(2027, 3, 1),
    chief_investigator="Test",
    sponsor="Test",
    ethics_reference="TEST",
    criteria=[
        Criterion(
            id="C1",
            type="inclusion",
            kind="notes",
            description="Pneumonia confirmed by consolidation on chest X-ray during this admission",
        ),
        Criterion(id="C2", type="exclusion", kind="notes", description="COPD requiring long-term home oxygen"),
    ],
)

# patient -> criterion -> correct verdict. None = not asked (patient already dropped).
EXPECTED = {
    "P001": {"C1": "met", "C2": "not_met"},
    "P002": {"C1": None, "C2": "met"},
    "P003": {"C1": "insufficient", "C2": "not_met"},
    "P004": {"C1": "met", "C2": "not_met"},
    "P005": {"C1": "insufficient", "C2": "insufficient"},
}
CASE = {
    "P001": "clear match",
    "P002": "clear exclusion",
    "P003": "ambiguous X-ray",
    "P004": "negation trap",
    "P005": "no evidence (I don't know)",
}

PATIENTS = [
    ("P001", "1958-03-14", "F"),
    ("P002", "1949-07-02", "M"),
    ("P003", "1971-11-23", "F"),
    ("P004", "1955-01-30", "M"),
    ("P005", "1966-05-09", "F"),
]

# (NoteID, EncounterID, patient, NoteType, AuthorRole, CreatedDateTime, NoteText)
NOTES = [
    ("N-1001", "E-101", "P001", "clerking", "FY2", "2026-11-10T09:15:00",
     "PC: 3/7 productive cough, fever, SOB. PMH: HTN. No known lung disease, never smoked. "
     "O/E: crackles R base. CXR: right lower lobe consolidation. Imp: CAP, CURB-65 1. Plan: amoxicillin PO."),
    ("N-1002", "E-101", "P001", "ward round", "Consultant", "2026-11-11T10:00:00",
     "Improving on abx. CXR reviewed: RLL consolidation confirmed. Sats 95% RA."),
    ("N-2001", "E-201", "P002", "clerking", "SpR", "2026-11-09T22:40:00",
     "PC: increasing SOB and green sputum. PMH: COPD, on home O2 2L via concentrator 16h/day (LTOT). "
     "CXR: left lower zone consolidation. Imp: CAP on background COPD."),
    ("N-3001", "E-301", "P003", "clerking", "FY1", "2026-11-12T14:05:00",
     "PC: cough and fever 2/7. PMH: asthma (childhood), no other resp hx. "
     "CXR: ?subtle LLL shadowing vs atelectasis, formal radiology report awaited. "
     "Imp: ?LRTI ?early pneumonia. Plan: repeat CXR in 48h."),
    ("N-4001", "E-401", "P004", "clerking", "SpR", "2026-11-08T08:30:00",
     "PC: fever, rigors, cough. PMH: COPD (mild, GOLD 1) on inhalers only. NOT on home oxygen, no LTOT. "
     "CXR: right middle lobe consolidation. Imp: CAP."),
    ("N-5001", "E-501", "P005", "nursing", "Staff Nurse", "2026-11-12T20:00:00",
     "Pt settled overnight, eating and drinking well. Mobilising with frame. Family visited."),
]

PATHOLOGY = [
    ("L-1001", "E-101", "P001", "CRP", "C-reactive protein", 148, "mg/L", 0, 5, "2026-11-10T09:00:00"),
    ("L-2001", "E-201", "P002", "CRP", "C-reactive protein", 96, "mg/L", 0, 5, "2026-11-09T22:00:00"),
    ("L-3001", "E-301", "P003", "CRP", "C-reactive protein", 41, "mg/L", 0, 5, "2026-11-12T13:50:00"),
    ("L-4001", "E-401", "P004", "CRP", "C-reactive protein", 210, "mg/L", 0, 5, "2026-11-08T08:00:00"),
]

DIAGNOSES = [
    ("D-1001", "E-101", "P001", "J18.9", "Pneumonia, unspecified", "primary"),
    ("D-2001", "E-201", "P002", "J18.9", "Pneumonia, unspecified", "primary"),
    ("D-2002", "E-201", "P002", "J44.9", "Chronic obstructive pulmonary disease, unspecified", "secondary"),
    ("D-4001", "E-401", "P004", "J18.9", "Pneumonia, unspecified", "primary"),
    ("D-4002", "E-401", "P004", "J44.9", "Chronic obstructive pulmonary disease, unspecified", "secondary"),
    ("D-5001", "E-501", "P005", "R26.8", "Other abnormalities of gait and mobility", "primary"),
]

OBSERVATIONS = [
    ("O-1001", "E-101", "P001", "2026-11-10T09:05:00", 22, 94, 0, 38.4, 128, 76, 98, "A"),
    ("O-2001", "E-201", "P002", "2026-11-09T22:10:00", 26, 89, 1, 38.1, 110, 70, 104, "A"),
    ("O-3001", "E-301", "P003", "2026-11-12T14:00:00", 18, 97, 0, 37.9, 122, 80, 90, "A"),
    ("O-4001", "E-401", "P004", "2026-11-08T08:10:00", 24, 93, 0, 39.0, 118, 72, 110, "A"),
    ("O-5001", "E-501", "P005", "2026-11-12T20:00:00", 16, 97, 0, 36.8, 132, 84, 78, "A"),
]

# Fake contact details: these must NEVER appear in anything sent to a model.
CONTACTS = [
    (pid, f"07700 900{n:03d}", None, f"patient{n}@example.com", None, None)
    for n, (pid, _, _) in enumerate(PATIENTS, 101)
]


def build_db(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Create the warehouse schema and load the five patients. Returns an open connection."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    conn.executemany("INSERT INTO Patients (LocalPatientIdentifier, DateOfBirth, Sex) VALUES (?,?,?)", PATIENTS)
    conn.executemany("INSERT INTO PatientContacts VALUES (?,?,?,?,?,?)", CONTACTS)
    for eid, pid in {(n[1], n[2]) for n in NOTES}:
        conn.execute(
            "INSERT INTO Inpatients (EncounterID, LocalPatientIdentifier, AdmissionDateTime) VALUES (?,?,?)",
            (eid, pid, "2026-11-08T00:00:00"),
        )
    conn.executemany("INSERT INTO ClinicalNotes VALUES (?,?,?,?,?,?,?)", NOTES)
    conn.executemany("INSERT INTO Pathology VALUES (?,?,?,?,?,?,?,?,?,?)", PATHOLOGY)
    conn.executemany("INSERT INTO Diagnoses VALUES (?,?,?,?,?,?)", DIAGNOSES)
    conn.executemany("INSERT INTO Observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", OBSERVATIONS)
    conn.commit()
    return conn
