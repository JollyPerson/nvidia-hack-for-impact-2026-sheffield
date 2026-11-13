"""Hand-labelled evaluation patients for the two seeded studies (AGENTS.md: hero patients with known answers).

Each case is one synthetic patient with a few clinical notes, the correct verdict for every NOTES criterion of
its study, and whether the patient is truly eligible. Structured criteria (age, CURB-65, eGFR, ...) are checked
by plain SQL in the prefilter, so every case is written to have already passed them.

Traps covered: negation ("not on home O2"), look-alikes (inhaled vs high-dose oral steroids, DNACPR vs
end-of-life, type 2 troponin rise vs ACS), timing (old CAP, hospital-acquired pneumonia), an ambiguous X-ray,
and exclusions the notes never mention (correct answer: insufficient, which code treats as "not mentioned").

eligible: True / False, or None when a clinician could not decide from the notes either (the right outcome is
then "needs review"); None cases are left out of recall and precision and reported separately.
All data is fictional.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"
REFERENCE_DATE = "2026-11-13"

MET, NOT, INS = "met", "not_met", "insufficient"


@dataclass
class Case:
    pid: str
    study: str  # "PNEUMONIA" or "HEART_FAILURE" (data/seed_studies.py)
    what: str
    eligible: bool | None
    labels: dict[str, str]  # notes criterion id -> correct verdict
    notes: list[tuple[str, str, str]] = field(default_factory=list)  # (NoteType, CreatedDateTime, text)
    dob: str = "1950-01-01"
    sex: str = "F"


CAP = "PNEUMONIA"  # C3 incl: CAP dx <24h | C5 excl: COPD on home O2 | C6 excl: immunosuppressed | C7 excl: capacity/EoL
HF = "HEART_FAILURE"  # C2 incl: new/worse HF needing IV diuretics | C6 excl: ACS as cause | C7 excl: capacity/EoL

T0 = "2026-11-12T22:10:00"
T1 = "2026-11-13T08:30:00"

CASES = [
    Case("E01", CAP, "clear match, explicit negatives", True,
         {"C3": MET, "C5": NOT, "C6": NOT, "C7": NOT},
         [("clerking", T0, "72M. PC: 3/7 productive cough, fever, SOB. PMH: HTN, T2DM. No chronic lung disease, "
                           "not on home oxygen. DH: metformin, ramipril. No immunosuppressants, no steroids. "
                           "CXR today: right lower lobe consolidation. Imp: community-acquired pneumonia, CURB-65 2. "
                           "Alert, has capacity, discussed and keen for research."),
          ("ward round", T1, "CAP day 1. CXR RLL consolidation confirmed by radiology. Capacity intact.")],
         sex="M"),
    Case("E02", CAP, "clear match, exclusions never mentioned", True,
         {"C3": MET, "C5": INS, "C6": INS, "C7": INS},
         [("clerking", T0, "68F. Fever, rigors, pleuritic chest pain 2/7. Crackles L base. "
                           "CXR: left lower zone consolidation. Imp: CAP, CURB-65 2. Plan: IV co-amoxiclav + clari.")]),
    Case("E03", CAP, "COPD on long-term home oxygen", False,
         {"C3": MET, "C5": MET, "C6": INS, "C7": INS},
         [("clerking", T0, "79M. Worsening SOB, green sputum. PMH: severe COPD on LTOT 2L via concentrator 15h/day. "
                           "CXR: right mid zone consolidation. Imp: CAP on background COPD.")], sex="M"),
    Case("E04", CAP, "negation trap: COPD but NOT on home oxygen", True,
         {"C3": MET, "C5": NOT, "C6": INS, "C7": INS},
         [("clerking", T0, "66F. Fever, cough. PMH: mild COPD (GOLD 1) on inhalers only, NOT on home oxygen, no LTOT. "
                           "CXR: RML consolidation. Imp: CAP.")]),
    Case("E05", CAP, "on chemotherapy", False,
         {"C3": MET, "C5": INS, "C6": MET, "C7": INS},
         [("clerking", T0, "58F. PMH: breast ca, currently on adjuvant chemotherapy (paclitaxel cycle 3, last dose "
                           "6/7 ago). Fever and cough. Neuts 2.1. CXR: LLL consolidation. Imp: CAP, not neutropenic.")]),
    Case("E06", CAP, "look-alike: inhaled steroid only", True,
         {"C3": MET, "C5": INS, "C6": NOT, "C7": INS},
         [("clerking", T0, "61M. Asthma on Clenil inhaler only - no oral steroids, no other immunosuppression. "
                           "Fever, productive cough. CXR: right lower lobe consolidation. Imp: CAP.")], sex="M"),
    Case("E07", CAP, "renal transplant on tacrolimus", False,
         {"C3": MET, "C5": INS, "C6": MET, "C7": INS},
         [("clerking", T0, "54M. PMH: renal transplant 2019, on tacrolimus and mycophenolate. Cough, fever 3/7. "
                           "CXR: bilateral lower zone consolidation. Imp: CAP in immunosuppressed pt.")], sex="M"),
    Case("E08", CAP, "end-of-life care", False,
         {"C3": MET, "C5": INS, "C6": INS, "C7": MET},
         [("clerking", T0, "91F. Metastatic lung ca. Fever, cough. CXR: RLL consolidation. Imp: CAP."),
          ("ward round", T1, "Discussed with family: for end-of-life care, comfort measures only, palliative team "
                             "involved. Abx stopped.")], dob="1935-04-02"),
    Case("E09", CAP, "lacks capacity (advanced dementia)", False,
         {"C3": MET, "C5": INS, "C6": INS, "C7": MET},
         [("clerking", T0, "86M. Advanced dementia, NH resident. Fever, cough, reduced intake. CXR: LLL consolidation. "
                           "Imp: CAP. MCA assessment: lacks capacity to consent to treatment decisions; "
                           "daughter is LPA for health.")], dob="1940-02-10", sex="M"),
    Case("E10", CAP, "ambiguous X-ray", None,
         {"C3": INS, "C5": INS, "C6": INS, "C7": INS},
         [("clerking", T0, "64F. Cough, low-grade temp. CXR: ?subtle LLL shadowing vs atelectasis, formal report "
                           "awaited. Imp: ?LRTI ?early pneumonia. Plan: repeat CXR 48h.")]),
    Case("E11", CAP, "timing trap: pneumonia was 3 weeks ago", False,
         {"C3": NOT, "C5": INS, "C6": INS, "C7": INS},
         [("clerking", T0, "75F. Mechanical fall at home, no head injury. PMH: CAP 3 weeks ago, completed abx, "
                           "fully recovered. Chest clear. CXR today: clear lung fields, no consolidation.")]),
    Case("E12", CAP, "hospital-acquired, not community-acquired", False,
         {"C3": NOT, "C5": INS, "C6": INS, "C7": INS},
         [("ward round", T1, "Day 7 post hip hemiarthroplasty. New fever, cough, O2 requirement. CXR: new RLL "
                             "consolidation. Imp: hospital-acquired pneumonia. Start pip-taz per HAP guideline.")]),
    Case("E13", CAP, "look-alike: DNACPR but has capacity, full treatment", True,
         {"C3": MET, "C5": INS, "C6": INS, "C7": NOT},
         [("clerking", T0, "82M. Fever, cough. CXR: right lower lobe consolidation. Imp: CAP. DNACPR in place (frailty) "
                           "but for full active treatment otherwise, not end-of-life. Has capacity, keen to take part "
                           "in research.")], dob="1944-06-30", sex="M"),
    Case("H01", HF, "clear match", True,
         {"C2": MET, "C6": NOT, "C7": INS},
         [("clerking", T0, "77M. 2/52 worsening SOB, orthopnoea, leg oedema to knees. Known HFrEF. Trop flat x2, "
                           "ECG no acute ischaemic changes - not ACS. Imp: decompensated heart failure. "
                           "Plan: IV furosemide 80mg BD, daily weights.")], sex="M"),
    Case("H02", HF, "ACS caused the admission", False,
         {"C2": MET, "C6": MET, "C7": INS},
         [("clerking", T0, "69M. Central chest pain then acute pulmonary oedema. Trop 45 -> 890. ECG: ST depression "
                           "V4-6. Imp: NSTEMI precipitating acute LVF. IV furosemide, for angiogram.")], sex="M"),
    Case("H03", HF, "worse HF but oral diuretics only", False,
         {"C2": NOT, "C6": INS, "C7": INS},
         [("clerking", T0, "80F. Mild ankle swelling, weight up 1kg. Known HFpEF. Comfortable at rest. "
                           "Plan: increase oral furosemide 40 -> 80mg OD; no IV diuretics needed. Home tomorrow.")]),
    Case("H04", HF, "look-alike: troponin rise not ACS", True,
         {"C2": MET, "C6": NOT, "C7": INS},
         [("clerking", T0, "73F. Worsening breathlessness, bibasal creps, oedema. Trop 32 -> 35, flat - type 2 "
                           "demand rise, cardiology: not ACS. Imp: acute decompensated HF. IV furosemide infusion.")]),
    Case("H05", HF, "end-of-life care", False,
         {"C2": MET, "C6": INS, "C7": MET},
         [("clerking", T0, "88F. End-stage HF, NYHA IV. Breathless at rest, anasarca. IV furosemide for comfort."),
          ("ward round", T1, "Palliative care review: end-of-life care, comfort measures, family aware.")],
         dob="1938-01-15"),
]


def build_db(path: str | Path) -> sqlite3.Connection:
    """Write every case into a fresh warehouse file (schema.sql). Returns an open connection."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text())
    for c in CASES:
        enc = f"EV-{c.pid}"
        conn.execute("INSERT INTO Patients (LocalPatientIdentifier, DateOfBirth, Sex) VALUES (?,?,?)",
                     (c.pid, c.dob, c.sex))
        conn.execute("INSERT INTO Inpatients (EncounterID, LocalPatientIdentifier, AdmissionDateTime) VALUES (?,?,?)",
                     (enc, c.pid, T0))
        for n, (ntype, when, text) in enumerate(c.notes, 1):
            conn.execute("INSERT INTO ClinicalNotes VALUES (?,?,?,?,?,?,?)",
                         (f"N-{c.pid}-{n}", enc, c.pid, ntype, "Doctor", when, text))
    conn.commit()
    return conn
