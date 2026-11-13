"""Build data/warehouse.db from data/schema.sql with fully synthetic patients.

Run from the project root:

    python data/generate.py

Output is identical on every run: one seeded random generator drives all
choices, Faker is seeded with the same value, and "now" is a fixed date rather
than the system clock.

The dataset models a district general hospital rather than a pneumonia
cohort. Encounters are allocated from three case-mix tables (emergency
admissions, elective admissions, ED-only attendances), plus a small
paediatric mix. Each encounter is then given to a patient whose age and
long-term conditions fit it (DKA only for type 1 diabetics, hip fracture only
for older patients, and so on).

What comes from where (see AGENTS.md):
- Faker (en_GB): dates of birth, postcodes, names used for contact details.
- Rule-based logic: everything clinical. Each encounter has a condition and a
  severity. The condition and severity drive the vitals (and so NEWS2) and the
  labs. Severity drives ED acuity and length of stay. Secondary diagnoses such
  as AKI or hyponatraemia are added only when the generated labs show them.
- Fixed code lists: a curated subset of ICD-10 and OPCS-4 codes, defined below.

Clinical notes are not generated here yet. ClinicalNotes is created empty.

Contact details are safe by construction: mobiles come only from Ofcom's drama
range 07700 900000-900999, landlines only from the Sheffield drama range
0114 496 0000-0999, and emails only at example.com.
"""

from __future__ import annotations

import random
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from faker import Faker

# Seed and paths. AGENTS.md puts the seed in config.py; move it there once
# config.py exists.
SEED = 42
N_PATIENTS = 1000
NOW = datetime(2026, 11, 13, 9, 0)  # fixed "now" so output never depends on the clock
HISTORY_DAYS = 3 * 365  # encounters are spread over the last three years

DATA_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DATA_DIR / "schema.sql"
DB_PATH = DATA_DIR / "warehouse.db"

# Encounter volumes over the three-year history. Patients left with no
# encounter afterwards get one ED attendance, so ED-only totals end up higher.
N_EMERGENCY = 700  # adult emergency admissions, including those still in
N_ELECTIVE = 220  # adult elective admissions (inpatient and day case)
N_ED_ONLY = 450  # adult ED attendances not admitted
N_CHILD_ADMIT = 45
N_CHILD_ED = 80

# Demo guarantees. Everyone in N_CURRENT_INPATIENTS is still on a ward at NOW.
N_CURRENT_INPATIENTS = 20  # includes the recent CAP admissions below
N_RECENT_CAP = 6  # CAP admissions in the last RECENT_CAP_HOURS
RECENT_CAP_HOURS = 48
MIN_CAP_ADMISSIONS = 15

TABLES = [
    "Patients",
    "PatientContacts",
    "Inpatients",
    "ECDS",
    "Observations",
    "Pathology",
    "Diagnoses",
    "Procedures",
    "ClinicalNotes",
]


# ---------------------------------------------------------------------------
# Case mix
# ---------------------------------------------------------------------------

# Relative share of ADULT EMERGENCY ADMISSIONS by presenting condition.
#
# The ranking follows NHS Hospital Episode Statistics (HES) Admitted Patient
# Care: the largest single primary-diagnosis groups for emergency admissions
# in England are non-specific chest pain (R07), abdominal pain (R10),
# pneumonia (J18) and urinary tract infection (N39), followed by COPD (J44),
# sepsis (A41), cellulitis (L03), heart failure (I50), myocardial infarction
# (I21), falls and femoral fracture (S72), stroke (I63) and AKI (N17).
#
# The numbers themselves are ESTIMATED. They were not copied from a specific
# HES table; check them against the latest "Hospital Admitted Patient Care
# Activity" publication before quoting them. These conditions cover only
# part of all real emergency admissions, so each one's share here is larger
# than its share of the national total (pneumonia is roughly 4% nationally,
# about 9% of this list).
EMERGENCY_MIX = {
    "chest_pain": 11.0,  # R07 is the largest single group
    "abdo_pain": 10.0,  # R10
    "cap": 9.0,  # J18
    "uti": 8.0,  # N39
    "copd_exac": 6.0,  # J44
    "sepsis": 5.0,  # A41
    "cellulitis": 4.5,  # L03
    "hf": 4.0,  # I50
    "nstemi": 3.5,  # I21
    "unstable_angina": 1.5,  # I20 (estimated)
    "falls": 3.5,  # R29.6 (estimated; many falls are coded by injury instead)
    "nof": 3.0,  # S72
    "syncope": 3.0,  # R55
    "stroke": 3.0,  # I63
    "tia": 2.0,  # G45 (estimated)
    "aki": 2.5,  # N17
    "gi_bleed": 2.5,  # K92 (estimated)
    "appendicitis": 3.0,  # K35
    "cholecystitis": 2.0,  # K81 (estimated)
    "asthma_adult": 1.5,  # J45 (estimated)
    "hypo": 1.0,  # E16 (estimated)
    "dka": 0.8,  # E10.1 (estimated)
    "aspiration": 0.8,  # J69 (estimated)
}

# Adult elective admissions, including day cases. ESTIMATED: weighted
# towards the high-volume procedures a DGH runs (cataract, joints, general
# surgery). Electives come out at about a quarter of inpatient episodes here;
# in HES they are a larger share once all day cases are counted.
ELECTIVE_MIX = {
    "cataract": 30.0,
    "knee_oa": 14.0,
    "hip_oa": 12.0,
    "gallstones": 12.0,
    "hernia": 12.0,
}

# Adult ED attendances that end in discharge, not admission. ESTIMATED:
# about 70% of real ED attendances are not admitted; injuries and chest pain
# dominate.
ED_ONLY_MIX = {
    "chest_pain": 16.0,
    "abdo_pain": 10.0,
    "ankle_sprain": 9.0,
    "head_injury": 7.0,
    "wrist_fracture": 5.0,
    "syncope": 5.0,
    "cellulitis": 5.0,
    "uti": 4.0,
    "falls": 4.0,
    "asthma_adult": 2.0,
    "copd_exac": 2.0,
    "cap": 2.0,  # mild CAP treated at home: a near-miss for CAP studies
    "hypo": 1.5,
    "tia": 1.5,
}

# Paediatric mix. ESTIMATED.
CHILD_ADMIT_MIX = {
    "bronchiolitis": 30.0,
    "gastro_child": 25.0,
    "asthma_child": 20.0,
    "pneumonia_child": 15.0,
}
CHILD_ED_MIX = {
    "gastro_child": 25.0,
    "head_injury": 20.0,
    "ankle_sprain": 15.0,
    "wrist_fracture": 15.0,
    "asthma_child": 10.0,
}


# ---------------------------------------------------------------------------
# Fixed code lists
# ---------------------------------------------------------------------------

# Long-term conditions, recorded as secondary diagnoses on admissions.
COMORBIDITIES = {
    "HTN": ("I10", "Essential (primary) hypertension"),
    "T1DM": ("E10.9", "Insulin-dependent diabetes mellitus without complications"),
    "T2DM": ("E11.9", "Non-insulin-dependent diabetes mellitus without complications"),
    "COPD": ("J44.9", "Chronic obstructive pulmonary disease, unspecified"),
    "ASTHMA": ("J45.9", "Asthma, unspecified"),
    "CKD3": ("N18.3", "Chronic kidney disease, stage 3"),
    "CKD4": ("N18.4", "Chronic kidney disease, stage 4"),
    "AF": ("I48.9", "Atrial fibrillation and atrial flutter, unspecified"),
    "IHD": ("I25.9", "Chronic ischaemic heart disease, unspecified"),
    "HF": ("I50.9", "Heart failure, unspecified"),
    "DEMENTIA": ("F03", "Unspecified dementia"),
    "OBESITY": ("E66.9", "Obesity, unspecified"),
}

# Prevalence by age band: 16-39, 40-64, 65-79, 80+.
# CKD is assigned once, then split into stage 3 or 4.
COMORBIDITY_PREVALENCE = {
    "HTN": (0.08, 0.28, 0.50, 0.60),
    "T1DM": (0.006, 0.005, 0.004, 0.002),
    "T2DM": (0.03, 0.12, 0.20, 0.22),  # skipped if the patient has T1DM
    "COPD": (0.00, 0.06, 0.14, 0.15),
    "ASTHMA": (0.12, 0.09, 0.07, 0.06),
    "CKD": (0.00, 0.04, 0.14, 0.25),
    "AF": (0.00, 0.02, 0.12, 0.22),
    "IHD": (0.00, 0.06, 0.14, 0.18),
    "HF": (0.00, 0.02, 0.08, 0.14),
    "DEMENTIA": (0.00, 0.00, 0.05, 0.18),
    "OBESITY": (0.15, 0.22, 0.18, 0.10),
}

# Secondary diagnoses added only when the generated data supports them.
DERIVED_DIAGNOSES = {
    "AKI": ("N17.9", "Acute renal failure, unspecified"),
    "HYPONATRAEMIA": ("E87.1", "Hypo-osmolality and hyponatraemia"),
    "SEPSIS": ("A41.9", "Sepsis, unspecified"),
    "DELIRIUM": ("F05.9", "Delirium, unspecified"),
    "RESP_FAILURE": ("J96.0", "Acute respiratory failure"),
}

# Where a sepsis admission's infection came from. None = source not found.
SEPSIS_SOURCES = [
    (("N39.0", "Urinary tract infection, site not specified"), 0.45),
    (("L03.1", "Cellulitis of other parts of limb"), 0.15),
    ((None, None), 0.40),
]

# OPCS-4 codes. Check against the official OPCS-4 browser before relying on them.
PROCEDURES = {
    "NIV": ("E85.1", "Non-invasive ventilation NEC"),
    "CPAP": ("E85.2", "Continuous positive airway pressure"),
    "CT_HEAD": ("U05.1", "Computed tomography of head"),
    "PCI": (
        "K75.1",
        "Percutaneous transluminal balloon angioplasty and insertion of 1-2 "
        "drug-eluting stents into coronary artery",
    ),
    "HEMIARTHROPLASTY": ("W46.1", "Primary prosthetic replacement of head of femur using cement"),
    "THR": ("W37.1", "Primary total prosthetic replacement of hip joint using cement"),
    "TKR": ("W40.1", "Primary total prosthetic replacement of knee joint using cement"),
    "PHACO": ("C71.2", "Phakoemulsification of lens"),
    "LENS_IMPLANT": ("C75.1", "Insertion of prosthetic replacement for lens"),
    "APPENDICECTOMY": ("H01.9", "Unspecified emergency excision of appendix"),
    "CHOLECYSTECTOMY": ("J18.3", "Total cholecystectomy NEC"),
    "HERNIA_REPAIR": ("T20.2", "Primary repair of inguinal hernia using insert of prosthetic material"),
    "LAPAROSCOPIC": ("Y75.2", "Laparoscopic approach to abdominal cavity NEC"),
    "OGD": ("G45.9", "Unspecified diagnostic fibreoptic endoscopic examination of upper gastrointestinal tract"),
}

# Community-acquired pneumonia is coded with one of these primary codes.
CAP_CODES = [
    (("J18.9", "Pneumonia, unspecified"), 0.70),
    (("J18.1", "Lobar pneumonia, unspecified"), 0.20),
    (("J15.9", "Bacterial pneumonia, unspecified"), 0.10),
]
CAP_ICD10 = [code for (code, _), _ in CAP_CODES]

# Pathology: code -> (name, units, ref low, ref high). Sex-specific ranges below.
TESTS = {
    "HB": ("Haemoglobin", "g/L", None, None),
    "WBC": ("White cell count", "x10^9/L", 4.0, 11.0),
    "PLT": ("Platelets", "x10^9/L", 150, 400),
    "NA": ("Sodium", "mmol/L", 133, 146),
    "K": ("Potassium", "mmol/L", 3.5, 5.3),
    "UREA": ("Urea", "mmol/L", 2.5, 7.8),
    "CREA": ("Creatinine", "umol/L", None, None),
    "EGFR": ("eGFR (CKD-EPI)", "mL/min/1.73m2", 90, None),
    "CRP": ("C-reactive protein", "mg/L", 0, 5),
    "GLU": ("Glucose (random)", "mmol/L", 4.0, 7.8),
    "LAC": ("Lactate (venous)", "mmol/L", 0.5, 2.0),
    "TNT": ("Troponin T (high sensitivity)", "ng/L", 0, 14),
    "NTBNP": ("NT-proBNP", "ng/L", 0, 400),
    "HBA1C": ("HbA1c", "mmol/mol", 20, 41),
    "BILI": ("Bilirubin", "umol/L", 0, 21),
    "ALT": ("Alanine aminotransferase", "U/L", 0, 40),
    "ALP": ("Alkaline phosphatase", "U/L", 30, 130),
    "AMY": ("Amylase", "U/L", 28, 100),
    "KET": ("Ketones (blood beta-hydroxybutyrate)", "mmol/L", 0, 0.6),
    "HCO3": ("Bicarbonate (venous)", "mmol/L", 22, 29),
    "PH": ("pH (venous)", None, 7.35, 7.45),
}
HB_RANGE = {"Male": (130, 170), "Female": (115, 165), "child": (105, 140)}
CREA_RANGE = {"Male": (59, 104), "Female": (45, 84), "child": (15, 60)}
DECIMALS = {"WBC": 1, "K": 1, "UREA": 1, "GLU": 1, "LAC": 1, "KET": 1, "PH": 2}

BASE_PANEL = ["HB", "WBC", "PLT", "NA", "K", "UREA", "CREA", "EGFR", "CRP"]
LFT_PANEL = ("BILI", "ALT", "ALP")


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    """What brings a patient in, and how it changes their vitals and labs.

    vitals: change at full severity (1.0) as
        (resp rate up, SpO2 down, temperature up, heart rate up,
         systolic BP change, chance of new confusion).
    labs: acute change at full severity. Most keys are added to the
        patient's baseline; creat is a multiplier on baseline creatinine.
    """

    key: str
    label: str  # used in the case-mix summary
    icd10: str
    description: str
    complaints: tuple[str, ...]
    specialties: tuple[str, ...]
    vitals: tuple[float, float, float, float, float, float]
    labs: dict
    los_days: tuple[float, float] = (2, 5)  # length of stay at low and high severity
    bloods: float = 1.0  # chance blood tests are taken
    extra_tests: tuple[str, ...] = ()
    infection: bool = False


C = Condition
CONDITIONS = {
    c.key: c
    for c in [
        # --- Respiratory --------------------------------------------------
        C("cap", "Community-acquired pneumonia", "J18.9", "Pneumonia, unspecified",
          ("Shortness of breath", "Cough", "Fever", "Pleuritic chest pain"),
          ("Respiratory Medicine", "Acute Medicine", "Respiratory Medicine"),
          (14, 10, 2.2, 40, -45, 0.45),
          {"crp": 320, "wbc": 13, "urea": 9, "creat": 0.6, "na": -7},
          los_days=(2, 12), infection=True),
        C("aspiration", "Aspiration pneumonia", "J69.0", "Pneumonitis due to food and vomit",
          ("Shortness of breath", "Cough", "Confusion"),
          ("Geriatric Medicine", "Acute Medicine"),
          (12, 9, 1.6, 35, -35, 0.5),
          {"crp": 260, "wbc": 11, "urea": 8, "creat": 0.5, "na": -5},
          los_days=(4, 14), infection=True),
        C("copd_exac", "COPD exacerbation", "J44.1",
          "Chronic obstructive pulmonary disease with acute exacerbation, unspecified",
          ("Shortness of breath", "Wheeze", "Cough"),
          ("Respiratory Medicine", "Acute Medicine"),
          (12, 10, 0.6, 30, -5, 0.1),
          {"crp": 50, "wbc": 4, "urea": 2},
          los_days=(2, 9)),
        C("asthma_adult", "Asthma", "J45.9", "Asthma, unspecified",
          ("Shortness of breath", "Wheeze"),
          ("Respiratory Medicine", "Acute Medicine"),
          (14, 6, 0.2, 35, 0, 0.0),
          {"wbc": 3, "crp": 10},
          los_days=(1, 4)),
        # --- Infection ------------------------------------------------------
        C("sepsis", "Sepsis", "A41.9", "Sepsis, unspecified",
          ("Generally unwell", "Fever", "Confusion", "Rigors"),
          ("Acute Medicine", "General Medicine"),
          (14, 5, 2.0, 45, -50, 0.5),
          {"crp": 300, "wbc": 14, "urea": 9, "creat": 0.8, "na": -4},
          los_days=(4, 14), infection=True),
        C("uti", "Urinary tract infection", "N39.0", "Urinary tract infection, site not specified",
          ("Confusion", "Fever", "Dysuria", "Fall"),
          ("Acute Medicine", "Geriatric Medicine"),
          (4, 2, 1.8, 30, -25, 0.4),
          {"crp": 160, "wbc": 8, "urea": 5, "creat": 0.4, "na": -3},
          los_days=(1, 7), infection=True),
        C("cellulitis", "Cellulitis", "L03.1", "Cellulitis of other parts of limb",
          ("Red swollen leg", "Limb pain"),
          ("Acute Medicine", "General Medicine"),
          (2, 0, 1.4, 20, -10, 0.03),
          {"crp": 180, "wbc": 7},
          los_days=(2, 8), infection=True),
        # --- Cardiovascular -------------------------------------------------
        C("chest_pain", "Chest pain (non-specific)", "R07.4", "Chest pain, unspecified",
          ("Chest pain",), ("Acute Medicine", "Cardiology"),
          (2, 0, 0.0, 10, 10, 0.0), {},
          los_days=(0.5, 2), extra_tests=("TNT",)),
        C("nstemi", "Acute coronary syndrome: NSTEMI", "I21.4", "Acute subendocardial myocardial infarction",
          ("Chest pain",), ("Cardiology",),
          (4, 2, 0.0, 20, 15, 0.0), {"wbc": 2},
          los_days=(2, 6), extra_tests=("TNT",)),
        C("unstable_angina", "Acute coronary syndrome: unstable angina", "I20.0", "Unstable angina",
          ("Chest pain",), ("Cardiology",),
          (2, 0, 0.0, 12, 10, 0.0), {},
          los_days=(1, 4), extra_tests=("TNT",)),
        C("hf", "Heart failure", "I50.0", "Congestive heart failure",
          ("Shortness of breath", "Leg swelling", "Orthopnoea"),
          ("Cardiology", "General Medicine"),
          (10, 8, 0.0, 25, 10, 0.1),
          {"urea": 6, "creat": 0.35, "na": -5},
          los_days=(3, 12), extra_tests=("NTBNP", "TNT")),
        C("syncope", "Syncope and collapse", "R55", "Syncope and collapse",
          ("Collapse", "Dizziness"), ("Acute Medicine", "Geriatric Medicine"),
          (1, 0, 0.0, 5, -10, 0.0), {},
          los_days=(1, 3), bloods=0.9),
        # --- Neurology ------------------------------------------------------
        C("stroke", "Stroke", "I63.9", "Cerebral infarction, unspecified",
          ("Facial droop", "Limb weakness", "Speech disturbance"),
          ("Stroke Medicine",),
          (2, 1, 0.2, 5, 40, 0.35), {},
          los_days=(4, 20)),
        C("tia", "TIA", "G45.9", "Transient cerebral ischaemic attack, unspecified",
          ("Speech disturbance", "Limb weakness", "Visual disturbance"),
          ("Stroke Medicine",),
          (1, 0, 0.0, 5, 25, 0.05), {},
          los_days=(0.5, 2)),
        # --- Falls and frailty ----------------------------------------------
        C("falls", "Falls", "R29.6", "Tendency to fall, not elsewhere classified",
          ("Fall", "Off legs"), ("Geriatric Medicine", "Acute Medicine"),
          (2, 0, 0.0, 10, -10, 0.25), {"na": -3, "urea": 2},
          los_days=(2, 10)),
        C("nof", "Hip fracture", "S72.0", "Fracture of neck of femur",
          ("Fall", "Hip pain"), ("Trauma & Orthopaedics",),
          (2, 0, 0.2, 15, 0, 0.2), {"hb": -25, "wbc": 4},
          los_days=(5, 20)),
        # --- Renal ----------------------------------------------------------
        C("aki", "Acute kidney injury", "N17.9", "Acute renal failure, unspecified",
          ("Reduced urine output", "Vomiting", "Generally unwell"),
          ("Renal Medicine", "Acute Medicine"),
          (4, 1, 0.0, 15, -25, 0.2),
          {"urea": 16, "creat": 2.0, "k": 1.5},
          los_days=(3, 10)),
        # --- Gastrointestinal and surgical ----------------------------------
        C("gi_bleed", "Gastrointestinal bleed", "K92.2", "Gastrointestinal haemorrhage, unspecified",
          ("Haematemesis", "Melaena", "Collapse"),
          ("Gastroenterology", "General Surgery"),
          (4, 1, 0.0, 40, -45, 0.15),
          {"hb": -45, "urea": 10, "creat": 0.2},  # upper GI blood raises urea
          los_days=(2, 8)),
        C("abdo_pain", "Abdominal pain (non-specific)", "R10.4", "Other and unspecified abdominal pain",
          ("Abdominal pain", "Vomiting"), ("General Surgery",),
          (3, 0, 0.4, 15, 0, 0.0), {"crp": 30, "wbc": 3},
          los_days=(1, 3), extra_tests=LFT_PANEL + ("AMY",)),
        C("appendicitis", "Appendicitis", "K35.8", "Acute appendicitis, other and unspecified",
          ("Abdominal pain", "Right iliac fossa pain", "Vomiting"), ("General Surgery",),
          (4, 0, 1.3, 25, -5, 0.0), {"crp": 150, "wbc": 8},
          los_days=(1, 4), extra_tests=LFT_PANEL, infection=True),
        C("cholecystitis", "Cholecystitis", "K81.0", "Acute cholecystitis",
          ("Abdominal pain", "Right upper quadrant pain", "Vomiting"), ("General Surgery",),
          (4, 0, 1.3, 25, -10, 0.05),
          {"crp": 180, "wbc": 8, "bili": 25, "alp": 80, "alt": 60},
          los_days=(2, 6), extra_tests=LFT_PANEL + ("AMY",), infection=True),
        # --- Diabetes -------------------------------------------------------
        C("dka", "Diabetic ketoacidosis", "E10.1", "Insulin-dependent diabetes mellitus with ketoacidosis",
          ("Vomiting", "Abdominal pain", "Shortness of breath"),
          ("Diabetes & Endocrinology", "Acute Medicine"),
          (14, 0, 0.0, 35, -20, 0.3),
          {"glu": 22, "k": 1.2, "ket": 5.0, "hco3": -15, "ph": -0.3, "urea": 5, "creat": 0.4},
          los_days=(2, 5), extra_tests=("GLU", "KET", "HCO3", "PH")),
        C("hypo", "Hypoglycaemia", "E16.0", "Drug-induced hypoglycaemia without coma",
          ("Confusion", "Collapse", "Reduced consciousness"),
          ("Acute Medicine", "Diabetes & Endocrinology"),
          (2, 0, -0.3, 15, 5, 0.6),
          {"glu": -8},
          los_days=(0.5, 2), extra_tests=("GLU",)),
        # --- Children -------------------------------------------------------
        C("bronchiolitis", "Bronchiolitis", "J21.9", "Acute bronchiolitis, unspecified",
          ("Breathing difficulty", "Poor feeding", "Cough"),
          ("Paediatrics",),
          (20, 8, 1.0, 30, 0, 0.0),
          {"crp": 20, "wbc": 3},
          los_days=(1, 5), bloods=0.3, infection=True),
        C("asthma_child", "Asthma (child)", "J45.9", "Asthma, unspecified",
          ("Wheeze", "Breathing difficulty"),
          ("Paediatrics",),
          (14, 6, 0.2, 35, 0, 0.0),
          {"wbc": 3},
          los_days=(1, 3), bloods=0.1),
        C("gastro_child", "Gastroenteritis (child)", "A09.9", "Gastroenteritis and colitis of unspecified origin",
          ("Vomiting", "Diarrhoea"),
          ("Paediatrics",),
          (4, 0, 1.0, 25, -15, 0.0),
          {"urea": 3, "na": -3, "crp": 20},
          los_days=(1, 2), bloods=0.4, infection=True),
        C("pneumonia_child", "Pneumonia (child)", "J18.9", "Pneumonia, unspecified",
          ("Fever", "Cough", "Breathing difficulty"),
          ("Paediatrics",),
          (16, 7, 2.0, 35, -10, 0.0),
          {"crp": 200, "wbc": 10},
          los_days=(1, 5), bloods=0.8, infection=True),
        # --- Minor injuries (ED only) ---------------------------------------
        C("ankle_sprain", "Ankle sprain", "S93.4", "Sprain and strain of ankle",
          ("Ankle injury",), ("Emergency Medicine",),
          (0, 0, 0.0, 5, 5, 0.0), {}, bloods=0.0),
        C("wrist_fracture", "Wrist fracture", "S52.5", "Fracture of lower end of radius",
          ("Wrist injury", "Fall"), ("Emergency Medicine",),
          (1, 0, 0.0, 10, 5, 0.0), {}, bloods=0.0),
        C("head_injury", "Head injury", "S06.0", "Concussion",
          ("Head injury", "Fall"), ("Emergency Medicine",),
          (1, 0, 0.0, 5, 5, 0.0), {}, bloods=0.2),
        # --- Elective -------------------------------------------------------
        C("cataract", "Cataract surgery", "H25.9", "Senile cataract, unspecified",
          ("Elective admission",), ("Ophthalmology",),
          (0, 0, 0.0, 0, 0, 0.0), {}, los_days=(0, 0), bloods=0.0),
        C("hip_oa", "Hip replacement", "M16.1", "Other primary coxarthrosis",
          ("Elective admission",), ("Trauma & Orthopaedics",),
          (0, 0, 0.0, 0, 0, 0.0), {}, los_days=(2, 5)),
        C("knee_oa", "Knee replacement", "M17.1", "Other primary gonarthrosis",
          ("Elective admission",), ("Trauma & Orthopaedics",),
          (0, 0, 0.0, 0, 0, 0.0), {}, los_days=(2, 5)),
        C("gallstones", "Laparoscopic cholecystectomy", "K80.2", "Calculus of gallbladder without cholecystitis",
          ("Elective admission",), ("General Surgery",),
          (0, 0, 0.0, 0, 0, 0.0), {}, los_days=(0, 1), extra_tests=LFT_PANEL),
        C("hernia", "Inguinal hernia repair", "K40.9",
          "Unilateral or unspecified inguinal hernia, without obstruction or gangrene",
          ("Elective admission",), ("General Surgery",),
          (0, 0, 0.0, 0, 0, 0.0), {}, los_days=(0, 1), bloods=0.6),
    ]
}
del C

MINOR_INJURIES = ("ankle_sprain", "wrist_fracture", "head_injury")

WARDS = {
    "Respiratory Medicine": ["Respiratory Ward 1", "Respiratory Ward 2"],
    "Acute Medicine": ["Acute Medical Unit"],
    "General Medicine": ["Medical Ward 5", "Medical Ward 6"],
    "Geriatric Medicine": ["Elderly Care Ward 3", "Elderly Care Ward 4"],
    "Cardiology": ["Cardiology Ward", "Coronary Care Unit"],
    "Stroke Medicine": ["Hyperacute Stroke Unit"],
    "Renal Medicine": ["Renal Ward"],
    "Gastroenterology": ["Gastroenterology Ward"],
    "Diabetes & Endocrinology": ["Endocrine Ward"],
    "General Surgery": ["Surgical Assessment Unit", "General Surgery Ward 7"],
    "Trauma & Orthopaedics": ["Orthopaedic Ward"],
    "Ophthalmology": ["Eye Day Unit"],
    "Paediatrics": ["Children's Assessment Unit", "Children's Ward"],
}

# NHS ethnic category codes, weighted roughly to a northern English city.
ETHNIC_CATEGORIES = [
    ("A", 0.74), ("B", 0.01), ("C", 0.04), ("D", 0.01), ("E", 0.005),
    ("F", 0.01), ("G", 0.01), ("H", 0.01), ("J", 0.04), ("K", 0.005),
    ("L", 0.01), ("M", 0.01), ("N", 0.03), ("P", 0.005), ("R", 0.02),
    ("S", 0.02), ("Z", 0.03),
]

# Sheffield postcode districts; most patients are local.
LOCAL_DISTRICTS = [f"S{n}" for n in range(1, 37) if n not in (15, 16, 19, 23, 24, 27, 28, 29, 30, 31, 34)]


# ---------------------------------------------------------------------------
# Clinical helpers
# ---------------------------------------------------------------------------


def egfr_ckd_epi_2021(creat_umol: float, age: int, sex: str) -> float:
    """CKD-EPI 2021 creatinine equation (no race coefficient)."""
    scr = creat_umol / 88.4
    female = sex == "Female"
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    ratio = scr / kappa
    egfr = 142 * min(ratio, 1) ** alpha * max(ratio, 1) ** -1.200 * 0.9938**age
    return egfr * 1.012 if female else egfr


def creatinine_for_egfr(target: float, age: int, sex: str) -> float:
    """Inverse of the eGFR equation by bisection (eGFR falls as creatinine rises)."""
    low, high = 10.0, 2000.0
    for _ in range(60):
        mid = (low + high) / 2
        if egfr_ckd_epi_2021(mid, age, sex) > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def age_on(dob: date, day: date) -> int:
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def apportion(mix: dict[str, float], total: int) -> dict[str, int]:
    """Split `total` across `mix` in proportion to the weights (largest remainder)."""
    weight_sum = sum(mix.values())
    exact = {key: total * weight / weight_sum for key, weight in mix.items()}
    counts = {key: int(value) for key, value in exact.items()}
    leftover = total - sum(counts.values())
    by_remainder = sorted(exact, key=lambda key: exact[key] - counts[key], reverse=True)
    for key in by_remainder[:leftover]:
        counts[key] += 1
    return counts


@dataclass
class Patient:
    mrn: str
    dob: date
    sex: str
    first_name: str
    surname: str
    comorbidities: list[str]
    base: dict  # baseline labs and vital-sign offsets

    def has(self, key: str) -> bool:
        return key in self.comorbidities

    @property
    def has_ckd(self) -> bool:
        return self.has("CKD3") or self.has("CKD4")

    @property
    def diabetic(self) -> bool:
        return self.has("T1DM") or self.has("T2DM")


def suitability(p: Patient, key: str, age: int) -> float:
    """How likely this patient is to present with this condition (0 = never).

    Encodes the clinical rules that keep each patient coherent: COPD
    exacerbations only in COPD patients, DKA only in type 1 diabetes, and so on.
    """
    has = p.has
    adult = age >= 16
    female = p.sex == "Female"
    rules = {
        # Adult emergencies
        "cap": adult * (2 if has("COPD") else 1),
        "aspiration": (age >= 75) * (4 if has("DEMENTIA") else 1),
        "copd_exac": adult * has("COPD"),
        "asthma_adult": adult * has("ASTHMA"),
        "sepsis": adult * (1.5 if has("DEMENTIA") or p.diabetic else 1),
        "uti": adult * (1.5 if female else 0.7) * (1.5 if has("DEMENTIA") else 1),
        "cellulitis": adult * (1.5 if has("OBESITY") or p.diabetic else 1),
        "chest_pain": adult * 1,
        "nstemi": (age >= 40) * (3 if has("IHD") else 1) * (1.3 if not female else 1),
        "unstable_angina": (age >= 40) * (3 if has("IHD") else 1) * (1.3 if not female else 1),
        "hf": (age >= 40) * (5 if has("HF") else 1 if age >= 70 else 0),
        "syncope": adult * 1,
        "stroke": (age >= 45) * (3 if has("AF") else 1),
        "tia": (age >= 45) * (3 if has("AF") else 1),
        "falls": (age >= 70) * (2 if has("DEMENTIA") else 1),
        "nof": (age >= 65) * (2 if has("DEMENTIA") else 1) * (1.5 if female else 1),
        "aki": (age >= 50) * (3 if p.has_ckd else 1),
        "gi_bleed": (age >= 40) * 1,
        "abdo_pain": adult * 1,
        "appendicitis": adult * (3 if age < 40 else 1 if age < 60 else 0.3),
        "cholecystitis": (age >= 25) * (2 if female else 1) * (1.5 if has("OBESITY") else 1),
        "dka": adult * has("T1DM"),
        "hypo": adult * (5 if has("T1DM") else 1 if has("T2DM") else 0),
        # Electives
        "cataract": (age >= 55) * age / 60,
        "hip_oa": (age >= 50) * 1,
        "knee_oa": (age >= 50) * 1,
        "gallstones": (25 <= age <= 80) * (2 if female else 1),
        "hernia": (age >= 25) * (4 if not female else 0.5),
        # Children
        "bronchiolitis": float(age < 2),
        "asthma_child": (2 <= age < 16) * (4 if has("ASTHMA") else 0.3),
        "gastro_child": float(age < 16),
        "pneumonia_child": float(age < 16),
        # Injuries (any age)
        "ankle_sprain": float(5 <= age < 70),
        "wrist_fracture": float(age >= 4) * (2 if age >= 65 and female else 1),
        "head_injury": 1.0,
    }
    return float(rules[key])


def admission_age_weight(age: int) -> float:
    """Emergency admission rates rise steeply with age (ESTIMATED from the HES age profile)."""
    if age < 16:
        return 1.0
    return 0.5 if age < 40 else 1.0 if age < 65 else 2.0 if age < 80 else 2.5


@dataclass
class Event:
    condition: Condition
    kind: str  # "admit" (ED then ward), "ed" (ED only), "elective"
    start: datetime
    severity: float  # 0 = well, 1 = most unwell
    ed_hours: float
    los_hours: float

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.ed_hours + self.los_hours)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class Warehouse:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.fake = Faker("en_GB")
        self.fake.seed_instance(seed)
        self.rows: dict[str, list[dict]] = {t: [] for t in TABLES}
        self.counters: dict[str, int] = defaultdict(int)
        self.patients: list[Patient] = []
        self.schedule: dict[str, list[Event]] = {}
        self.unplaced: Counter = Counter()  # encounters no suitable patient could take
        self._weights: dict[tuple[str, str], list[float]] = {}
        # Safe contact number pools, shuffled once and drawn without replacement.
        self.mobiles = [f"07700 900{n:03d}" for n in range(1000)]
        self.landlines = [f"0114 496 {n:04d}" for n in range(1000)]
        self.rng.shuffle(self.mobiles)
        self.rng.shuffle(self.landlines)

    def next_id(self, prefix: str, width: int) -> str:
        self.counters[prefix] += 1
        return f"{prefix}-{self.counters[prefix]:0{width}d}"

    def weighted(self, options):
        items, weights = zip(*options)
        return self.rng.choices(items, weights=weights, k=1)[0]

    def draw_number(self, pool: list[str]) -> str:
        if not pool:
            raise RuntimeError("Ran out of safe drama-range phone numbers; lower N_PATIENTS.")
        return pool.pop()

    # -- patients ----------------------------------------------------------

    def make_patient(self, index: int) -> Patient:
        rng, fake = self.rng, self.fake
        band = self.weighted([((0, 15), 10), ((16, 39), 15), ((40, 64), 25), ((65, 79), 28), ((80, 97), 22)])
        age = rng.randint(*band)
        today = NOW.date()
        dob = fake.date_between_dates(
            date_start=date(today.year - age - 1, today.month, today.day) + timedelta(days=1),
            # At least 30 days old, so even a newborn has time for an attendance.
            date_end=min(date(today.year - age, today.month, today.day), today - timedelta(days=30)),
        )
        sex = rng.choice(["Male", "Female"])
        first_name = fake.first_name_male() if sex == "Male" else fake.first_name_female()
        surname = fake.last_name()
        patient = Patient(
            mrn=f"MRN{100001 + index}",
            dob=dob,
            sex=sex,
            first_name=first_name,
            surname=surname,
            comorbidities=self.pick_comorbidities(age),
            base={},
        )
        patient.base = self.baseline(patient, age)

        if rng.random() < 0.85:
            district = rng.choice(LOCAL_DISTRICTS)
        else:
            district = fake.postcode().split()[0]
        self.rows["Patients"].append({
            "LocalPatientIdentifier": patient.mrn,
            "DateOfBirth": dob.isoformat(),
            "Sex": sex,
            "EthnicCategory": self.weighted(ETHNIC_CATEGORIES),
            "PostcodeDistrict": district,
        })
        self.add_contacts(patient, age)
        return patient

    def pick_comorbidities(self, age: int) -> list[str]:
        rng = self.rng
        if age < 16:
            return ["ASTHMA"] if rng.random() < 0.10 else []
        band = 0 if age < 40 else 1 if age < 65 else 2 if age < 80 else 3
        found = []
        for key, prevalence in COMORBIDITY_PREVALENCE.items():  # dict order is fixed
            if rng.random() < prevalence[band]:
                if key == "T2DM" and "T1DM" in found:
                    continue
                if key == "CKD":
                    found.append("CKD4" if rng.random() < 0.2 else "CKD3")
                else:
                    found.append(key)
        if "HF" in found and "IHD" not in found and rng.random() < 0.5:
            found.append("IHD")
        return found

    def baseline(self, p: Patient, age: int) -> dict:
        """Per-patient normal values. Acute illness is added on top of these."""
        rng = self.rng
        if age < 16:
            hb, creat = rng.uniform(110, 135), 15 + age * 3 + rng.uniform(-3, 5)
        else:
            hb = rng.uniform(135, 160) if p.sex == "Male" else rng.uniform(120, 145)
            if p.has("CKD4"):
                target = rng.uniform(16, 29)
            elif p.has("CKD3"):
                target = rng.uniform(32, 58)
            elif age < 40:
                target = rng.uniform(90, 120)
            elif age < 65:
                target = rng.uniform(70, 105)
            elif age < 80:
                target = rng.uniform(62, 90)
            else:
                target = rng.uniform(60, 80)
            creat = creatinine_for_egfr(target, age, p.sex)
            if p.has_ckd:
                hb -= rng.uniform(5, 15)  # anaemia of CKD
        urea = rng.uniform(3.0, 6.5) + (1.5 if age >= 70 else 0)
        urea += 4 if p.has("CKD3") else 9 if p.has("CKD4") else 0
        return {
            "hb": hb,
            "wbc": rng.uniform(5.0, 9.0),
            "plt": rng.uniform(170, 380),
            "na": rng.uniform(137, 142),
            "k": rng.uniform(3.8, 4.6) + (0.4 if p.has_ckd else 0),
            "urea": urea,
            "creat": creat,
            "crp": rng.uniform(0.5, 4.5),
            "glu": rng.uniform(7.0, 11.0) if p.diabetic else rng.uniform(4.5, 6.5),
            "hba1c": rng.uniform(50, 85) if p.diabetic else rng.uniform(30, 41),
            "bili": rng.uniform(4, 15),
            "alt": rng.uniform(12, 35),
            "alp": rng.uniform(50, 110),
            "amy": rng.uniform(30, 90),
            "ket": rng.uniform(0.1, 0.3),
            "hco3": rng.uniform(23, 28),
            "ph": rng.uniform(7.36, 7.42),
            "sbp": rng.uniform(110, 140) + (15 if p.has("HTN") else 0),
            "hr_offset": rng.uniform(-8, 8) + (8 if p.has("AF") else 0),
            "rr_offset": rng.uniform(-1.5, 1.5) + (2 if p.has("COPD") else 0),
            "spo2": rng.uniform(89, 93) if p.has("COPD") else rng.uniform(95, 99),
            "temp": rng.uniform(36.3, 37.0),
        }

    def add_contacts(self, p: Patient, age: int) -> None:
        rng = self.rng
        slug = re.sub(r"[^a-z]", "", p.first_name.lower()) + "." + re.sub(r"[^a-z]", "", p.surname.lower())
        if age < 16:
            mobile = self.draw_number(self.mobiles) if age >= 13 and rng.random() < 0.3 else None
            email = None
            guardian_first = self.fake.first_name_female() if rng.random() < 0.6 else self.fake.first_name_male()
            guardian_name = f"{guardian_first} {p.surname}"
            guardian_phone = self.draw_number(self.mobiles)
        else:
            mobile = self.draw_number(self.mobiles) if rng.random() < 0.85 else None
            email = f"{slug}{rng.randint(1, 99)}@example.com" if rng.random() < 0.65 else None
            guardian_name = guardian_phone = None
        landline = self.draw_number(self.landlines) if rng.random() < 0.4 else None
        self.rows["PatientContacts"].append({
            "LocalPatientIdentifier": p.mrn,
            "MobileNumber": mobile,
            "Landline": landline,
            "Email": email,
            "GuardianName": guardian_name,
            "GuardianPhone": guardian_phone,
        })

    # -- allocating encounters to patients --------------------------------

    def allocate(self) -> None:
        """Turn the case-mix tables into dated encounters for suitable patients.

        Order matters: current admissions are placed first, because nothing
        may be scheduled after a patient who is still on a ward.
        """
        rng = self.rng
        for _ in range(N_RECENT_CAP):
            self.place("cap", "admit", "recent")
        keys, weights = zip(*EMERGENCY_MIX.items())
        for key in rng.choices(keys, weights=weights, k=N_CURRENT_INPATIENTS - N_RECENT_CAP):
            self.place(key, "admit", "current")

        plan = [
            (EMERGENCY_MIX, N_EMERGENCY - N_CURRENT_INPATIENTS, "admit"),
            (ELECTIVE_MIX, N_ELECTIVE, "elective"),
            (ED_ONLY_MIX, N_ED_ONLY, "ed"),
            (CHILD_ADMIT_MIX, N_CHILD_ADMIT, "admit"),
            (CHILD_ED_MIX, N_CHILD_ED, "ed"),
        ]
        for mix, total, kind in plan:
            for key, count in apportion(mix, total).items():
                for _ in range(count):
                    self.place(key, kind, "past")

        # Everyone in the warehouse has attended at least once.
        for p in self.patients:
            if self.schedule[p.mrn]:
                continue
            age = age_on(p.dob, NOW.date())
            mix = CHILD_ED_MIX if age < 16 else ED_ONLY_MIX
            options = [(key, w * suitability(p, key, age)) for key, w in mix.items()]
            key = self.weighted([o for o in options if o[1] > 0])
            # Fallback for e.g. a new 16-year-old, whose history is mostly
            # too young for adult-only conditions.
            if not self.place(key, "ed", "past", patient=p, record_failure=False):
                self.place("head_injury", "ed", "past", patient=p)

    def pool_weights(self, key: str, kind: str) -> list[float]:
        if (key, kind) not in self._weights:
            weights = []
            for p in self.patients:
                age = age_on(p.dob, NOW.date())
                w = suitability(p, key, age)
                if kind == "admit":
                    w *= admission_age_weight(age)
                weights.append(w)
            self._weights[(key, kind)] = weights
        return self._weights[(key, kind)]

    def place(self, key: str, kind: str, timing: str, patient: Patient | None = None, record_failure: bool = True) -> bool:
        """Schedule one encounter. timing: "past", "current" or "recent" (CAP in last 48h)."""
        rng = self.rng
        for _ in range(60):
            p = patient or rng.choices(self.patients, weights=self.pool_weights(key, kind), k=1)[0]
            if timing == "past":
                max_days = min(HISTORY_DAYS, (NOW.date() - p.dob).days - 1)
                if max_days < 2:
                    continue
                day = (NOW - timedelta(days=rng.uniform(1, max_days))).date()
                ev = self.make_event(p, key, kind, day)
                if ev.end > NOW - timedelta(hours=1):
                    continue  # only the pinned admissions are still in
            else:
                ev = self.make_event(p, key, kind, NOW.date())
                if timing == "recent":
                    hours_ago = rng.uniform(ev.ed_hours + 1, RECENT_CAP_HOURS - 1)
                else:
                    hours_ago = rng.uniform(ev.ed_hours + 1, max(ev.ed_hours + 2, 0.9 * (ev.ed_hours + ev.los_hours)))
                ev.start = NOW - timedelta(hours=hours_ago)
                if ev.end <= NOW:  # make sure they are still on the ward
                    ev.los_hours += (NOW - ev.end).total_seconds() / 3600 + rng.uniform(12, 72)
            if ev.start.date() <= p.dob or suitability(p, key, age_on(p.dob, ev.start.date())) <= 0:
                continue
            if self.overlaps(p.mrn, ev):
                continue
            self.schedule[p.mrn].append(ev)
            return True
        if record_failure:
            self.unplaced[key] += 1
        return False

    def overlaps(self, mrn: str, ev: Event) -> bool:
        gap = timedelta(days=3)
        return any(ev.start < other.end + gap and other.start < ev.end + gap for other in self.schedule[mrn])

    def make_event(self, p: Patient, key: str, kind: str, day: date) -> Event:
        rng = self.rng
        cond = CONDITIONS[key]
        age = age_on(p.dob, day)

        if kind == "elective":
            start = datetime.combine(day, time(7, 30))
            los_hours = max(rng.uniform(4, 7), rng.uniform(*cond.los_days) * 24)
            return Event(cond, kind, start, 0.0, 0.0, los_hours)

        # ED arrivals peak in the afternoon.
        start = datetime.combine(day, time(0)) + timedelta(hours=rng.triangular(0, 24, 14))
        if kind == "admit":
            severity = rng.uniform(0.5, 1.0) if key == "sepsis" else rng.uniform(0.3, 1.0)
            ed_hours = rng.uniform(2, 9)
        else:
            severity = rng.uniform(0.05, 0.35)
            ed_hours = rng.uniform(1.5, 6)
        if cond.infection and age >= 75:
            severity = min(1.0, severity + 0.1)

        los_hours = 0.0
        if kind == "admit":
            low, high = cond.los_days
            los_days = (low + (high - low) * severity) * rng.uniform(0.7, 1.3)
            if age >= 80:
                los_days *= 1.3
            los_hours = max(12.0, los_days * 24)
        return Event(cond, kind, start, severity, ed_hours, los_hours)

    # -- simulating encounters --------------------------------------------

    def simulate(self, p: Patient, ev: Event) -> None:
        rng = self.rng
        cond = ev.condition
        age = age_on(p.dob, ev.start.date())
        state = {
            "confused": rng.random() < cond.vitals[5] * ev.severity + (0.15 if p.has("DEMENTIA") and cond.infection else 0),
            "improving": rng.random() > 0.1,
            "max_creat_ratio": 1.0,
            "min_na": 999.0,
            "on_oxygen": False,
            "cap_code": self.weighted(CAP_CODES) if cond.key == "cap" else None,
            "sepsis_source": self.weighted(SEPSIS_SOURCES) if cond.key == "sepsis" else (None, None),
        }
        takes_bloods = rng.random() < cond.bloods

        ed_end = ev.start
        if ev.kind in ("admit", "ed"):
            ed_end = self.simulate_ed(p, ev, age, state, takes_bloods)
        if ev.kind in ("admit", "elective"):
            self.simulate_inpatient(p, ev, age, state, ed_end, takes_bloods)

    def severity_at(self, ev: Event, t: datetime, improving: bool) -> float:
        total = (ev.end - ev.start).total_seconds()
        frac = clamp((t - ev.start).total_seconds() / total, 0, 1) if total else 0
        if improving:
            return ev.severity * (1 - frac) ** 1.3
        return ev.severity * (1 - 0.4 * frac)

    def simulate_ed(self, p: Patient, ev: Event, age: int, state: dict, takes_bloods: bool) -> datetime:
        rng = self.rng
        cond = ev.condition
        encounter_id = self.next_id("ED", 5)
        ed_end = ev.start + timedelta(hours=ev.ed_hours)
        minor = cond.key in MINOR_INJURIES or ev.severity < 0.15

        if minor:
            acuity = rng.choice([4, 5])
        elif ev.severity > 0.92:
            acuity = 1 if rng.random() < 0.4 else 2
        elif ev.severity > 0.6:
            acuity = 2
        elif ev.severity > 0.3:
            acuity = 3
        else:
            acuity = rng.choice([3, 4])

        if ev.kind == "admit":
            destination = "Admitted"
        else:
            destination = self.weighted([
                ("Discharged - GP follow-up", 5),
                ("Discharged - no follow-up", 4),
                ("Discharged - outpatient clinic", 1),
            ])
        complaint = "Confusion" if state["confused"] and rng.random() < 0.5 else rng.choice(cond.complaints)
        self.rows["ECDS"].append({
            "EncounterID": encounter_id,
            "LocalPatientIdentifier": p.mrn,
            "CheckInDateTime": iso(ev.start),
            "Acuity": acuity,
            "ChiefComplaint": complaint,
            "DischargeDestination": destination,
        })

        # Observations: on arrival, then every 1-2 hours while in ED.
        t = ev.start + timedelta(minutes=rng.uniform(5, 25))
        first = True
        while t < ed_end and t <= NOW:
            self.add_observation(p, ev, encounter_id, t, age, state, first)
            first = False
            if minor:
                break
            t += timedelta(hours=rng.uniform(1, 2))

        if takes_bloods:
            tests = self.panel(p, ev, age, in_ed=True)
            self.add_labs(p, ev, encounter_id, ev.start + timedelta(minutes=rng.uniform(30, 75)), age, state, tests)

        primary = state["cap_code"] or (cond.icd10, cond.description)
        self.add_diagnosis(p, encounter_id, primary, "primary")

        ct_chance = {"stroke": 1.0, "tia": 0.5, "head_injury": 0.5, "falls": 0.25}.get(cond.key, 0)
        if rng.random() < ct_chance:
            self.add_procedure(p, encounter_id, "CT_HEAD", ev.start + timedelta(minutes=rng.uniform(20, 90)))
        return ed_end

    def simulate_inpatient(self, p: Patient, ev: Event, age: int, state: dict, admitted: datetime, takes_bloods: bool) -> None:
        rng = self.rng
        cond = ev.condition
        encounter_id = self.next_id("IP", 5)
        discharge = ev.end
        still_in = discharge > NOW

        specialty = rng.choice(cond.specialties)
        if cond.key == "cap" and age >= 85 and rng.random() < 0.4:
            specialty = "Geriatric Medicine"
        self.rows["Inpatients"].append({
            "EncounterID": encounter_id,
            "LocalPatientIdentifier": p.mrn,
            "AdmissionDateTime": iso(admitted),
            "DischargeDateTime": None if still_in else iso(discharge),
            "AdmissionMethod": "Elective - waiting list" if ev.kind == "elective" else "Emergency - via ED",
            "Specialty": specialty,
            "Ward": rng.choice(WARDS[specialty]),
        })
        stop = min(discharge, NOW)

        # Observations: 4-hourly while unwell, 6-8 hourly once settling.
        t = admitted + timedelta(minutes=rng.uniform(20, 60))
        while t < stop:
            s = self.add_observation(p, ev, encounter_id, t, age, state, first=False)
            t += timedelta(hours=4 if s > 0.5 else rng.uniform(6, 8))

        # Labs: pre-op for electives; every morning for three days, then every other day.
        if takes_bloods:
            if ev.kind == "elective":
                self.add_labs(p, ev, encounter_id, admitted + timedelta(hours=1), age, state, self.panel(p, ev, age, in_ed=False))
            day = 1
            while True:
                t = datetime.combine(admitted.date() + timedelta(days=day), time(6)) + timedelta(minutes=rng.uniform(0, 90))
                if t >= stop:
                    break
                self.add_labs(p, ev, encounter_id, t, age, state, self.panel(p, ev, age, in_ed=False))
                day += 1 if day < 3 else 2

        self.add_procedures_for_admission(p, ev, encounter_id, admitted, stop, state)
        self.add_admission_diagnoses(p, ev, encounter_id, state)

    def add_observation(self, p: Patient, ev: Event, encounter_id: str, t: datetime, age: int, state: dict, first: bool) -> float:
        rng = self.rng
        b = p.base
        s = self.severity_at(ev, t, state["improving"])
        rr_d, spo2_d, temp_d, hr_d, sbp_d, _ = ev.condition.vitals

        if age < 1:
            rr0, hr0, sbp0 = 35, 130, 85
        elif age < 5:
            rr0, hr0, sbp0 = 27, 110, 92
        elif age < 12:
            rr0, hr0, sbp0 = 22, 95, 100
        elif age < 16:
            rr0, hr0, sbp0 = 17, 80, 110
        else:
            rr0, hr0, sbp0 = 16, 75, b["sbp"]

        rr = rr0 + b["rr_offset"] + rr_d * s + rng.gauss(0, 1.2)
        hr = hr0 + b["hr_offset"] + hr_d * s + rng.gauss(0, 4)
        sbp = sbp0 + sbp_d * s + rng.gauss(0, 6)
        dbp = sbp * rng.uniform(0.55, 0.65)
        temp = b["temp"] + temp_d * s + rng.gauss(0, 0.15)

        raw_spo2 = b["spo2"] - spo2_d * s + rng.gauss(0, 0.8)
        target = 88 if p.has("COPD") else 94
        on_o2 = False
        spo2 = raw_spo2
        if not first and raw_spo2 < target:  # the arrival set is on room air
            on_o2 = True
            state["on_oxygen"] = True
            ceiling = 92 if p.has("COPD") else 98
            spo2 = min(ceiling, raw_spo2 + rng.uniform(4, 9))

        consciousness = "A"
        if state["confused"] and s > 0.5 * ev.severity:
            consciousness = "C"
        if ev.condition.key == "stroke" and ev.severity > 0.85 and s > 0.6:
            consciousness = "V"

        self.rows["Observations"].append({
            "ObservationID": self.next_id("OBS", 6),
            "EncounterID": encounter_id,
            "LocalPatientIdentifier": p.mrn,
            "RecordedDateTime": iso(t),
            "RespRate": int(round(clamp(rr, 8, 60))),
            "SpO2": int(round(clamp(spo2, 75, 100))),
            "OnOxygen": int(on_o2),
            "Temperature": round(clamp(temp, 35.0, 41.0), 1),
            "SystolicBP": int(round(clamp(sbp, 70, 220))),
            "DiastolicBP": int(round(clamp(dbp, 35, 130))),
            "HeartRate": int(round(clamp(hr, 40, 190))),
            "Consciousness": consciousness,
        })
        return s

    def panel(self, p: Patient, ev: Event, age: int, in_ed: bool) -> list[str]:
        rng = self.rng
        cond = ev.condition
        tests = [code for code in BASE_PANEL if not (age < 16 and code == "EGFR")]
        if age < 16:
            return tests
        tests += [t for t in cond.extra_tests if t not in tests]
        if in_ed:
            if "GLU" not in tests:
                tests.append("GLU")
            if cond.infection and ev.severity > 0.55:
                tests.append("LAC")
            if "TNT" not in tests and cond.key in ("cap", "copd_exac", "sepsis") and age >= 70 and rng.random() < 0.3:
                tests.append("TNT")
            if p.diabetic and rng.random() < 0.5:
                tests.append("HBA1C")
        return tests

    def add_labs(self, p: Patient, ev: Event, encounter_id: str, t: datetime, age: int, state: dict, tests: list[str]) -> None:
        if t > NOW:
            return
        rng = self.rng
        b = p.base
        s = self.severity_at(ev, t, state["improving"])
        fx = ev.condition.labs
        key = ev.condition.key
        child = age < 16

        def shifted(name: str, noise: float = 0.0) -> float:
            return b[name] + fx.get(name, 0) * s + (rng.gauss(0, noise) if noise else 0)

        creat = b["creat"] * (1 + fx.get("creat", 0) * s) * rng.uniform(0.95, 1.05)
        wbc = b["wbc"] + fx.get("wbc", 0) * s * rng.uniform(0.7, 1.2)
        if key in ("cap", "sepsis") and ev.severity > 0.85 and age >= 75 and rng.random() < 0.15:
            wbc = rng.uniform(2.5, 3.8)  # leukopenia in severe infection
        hb = b["hb"] + fx.get("hb", 0) * (1 - s if key == "nof" else s) + rng.gauss(0, 3)
        values = {
            "HB": hb,
            "WBC": wbc,
            "PLT": b["plt"] + (60 * s if ev.condition.infection else 0) + rng.gauss(0, 15),
            "NA": shifted("na", 1),
            "K": shifted("k", 0.15),
            "UREA": b["urea"] + fx.get("urea", 0) * s * rng.uniform(0.7, 1.2),
            "CREA": creat,
            "EGFR": min(90, egfr_ckd_epi_2021(creat, age, p.sex)),
            "CRP": b["crp"] + fx.get("crp", 0) * s * rng.uniform(0.6, 1.1),
            "GLU": max(1.2, b["glu"] + fx.get("glu", 2) * s + rng.gauss(0, 0.4)),
            "LAC": 1.0 + 3.0 * s + rng.gauss(0, 0.2),
            "HBA1C": b["hba1c"],
            "BILI": shifted("bili", 1),
            "ALT": shifted("alt", 3),
            "ALP": shifted("alp", 5),
            "AMY": shifted("amy", 5),
            "KET": shifted("ket", 0.05),
            "HCO3": shifted("hco3", 0.5),
            "PH": shifted("ph", 0.01),
        }
        if key == "nstemi":
            values["TNT"] = 30 + 1500 * s * rng.uniform(0.5, 1.2)
        elif key == "hf":
            values["TNT"] = rng.uniform(15, 45)
        elif key in ("chest_pain", "unstable_angina"):
            values["TNT"] = rng.uniform(3, 12)
        else:
            values["TNT"] = rng.uniform(5, 22)
        if key == "hf":
            values["NTBNP"] = 1500 + 12000 * s * rng.uniform(0.6, 1.1)
        else:
            values["NTBNP"] = rng.uniform(50, 350) + age * 3

        state["max_creat_ratio"] = max(state["max_creat_ratio"], creat / b["creat"])
        state["min_na"] = min(state["min_na"], values["NA"])

        for code in tests:
            name, units, low, high = TESTS[code]
            if code == "HB":
                low, high = HB_RANGE["child" if child else p.sex]
            elif code == "CREA":
                low, high = CREA_RANGE["child" if child else p.sex]
            value = round(max(values[code], 0.1), DECIMALS.get(code, 0))
            if code not in DECIMALS:
                value = float(int(value))
            self.rows["Pathology"].append({
                "PathologyID": self.next_id("PATH", 6),
                "EncounterID": encounter_id,
                "LocalPatientIdentifier": p.mrn,
                "TestCode": code,
                "TestName": name,
                "Value": value,
                "Units": units,
                "RefLow": low,
                "RefHigh": high,
                "CollectedDateTime": iso(t),
            })

    def add_procedures_for_admission(self, p: Patient, ev: Event, encounter_id: str, admitted: datetime, stop: datetime, state: dict) -> None:
        rng = self.rng
        key = ev.condition.key
        s = ev.severity

        def at(hours_low: float, hours_high: float) -> datetime:
            return admitted + timedelta(hours=rng.uniform(hours_low, hours_high))

        planned = []
        if key in ("cap", "aspiration", "sepsis") and s > 0.8 and state["on_oxygen"] and rng.random() < 0.25:
            planned.append(("CPAP", at(2, 12)))
        if key == "copd_exac" and s > 0.65 and rng.random() < 0.4:
            planned.append(("NIV", at(1, 8)))
        if key == "hf" and s > 0.8 and rng.random() < 0.15:
            planned.append(("CPAP", at(1, 6)))
        if key == "nstemi" and rng.random() < 0.55:
            planned.append(("PCI", at(24, 72)))
        if key == "unstable_angina" and rng.random() < 0.3:
            planned.append(("PCI", at(24, 72)))
        if key == "nof" and rng.random() < 0.9:
            planned.append(("HEMIARTHROPLASTY", at(18, 48)))
        if key == "gi_bleed" and rng.random() < 0.85:
            planned.append(("OGD", at(6, 36)))
        if key == "appendicitis" and rng.random() < 0.9:
            t = at(4, 24)
            planned.append(("APPENDICECTOMY", t))
            if rng.random() < 0.85:
                planned.append(("LAPAROSCOPIC", t))
        if key == "hip_oa":
            planned.append(("THR", at(2, 4)))
        if key == "knee_oa":
            planned.append(("TKR", at(2, 4)))
        if key == "gallstones":
            t = at(1, 4)
            planned += [("CHOLECYSTECTOMY", t), ("LAPAROSCOPIC", t)]
        if key == "hernia":
            planned.append(("HERNIA_REPAIR", at(1, 4)))
        if key == "cataract":
            t = at(1, 2)
            planned += [("PHACO", t), ("LENS_IMPLANT", t)]

        for proc, t in planned:
            if t < stop:
                self.add_procedure(p, encounter_id, proc, t)
                if proc in ("CPAP", "NIV"):
                    state["ventilated"] = True

    def add_admission_diagnoses(self, p: Patient, ev: Event, encounter_id: str, state: dict) -> None:
        cond = ev.condition
        primary = state["cap_code"] or (cond.icd10, cond.description)
        self.add_diagnosis(p, encounter_id, primary, "primary")

        secondary = [COMORBIDITIES[c] for c in p.comorbidities]
        if state["sepsis_source"][0]:
            secondary.append(state["sepsis_source"])
        if state["max_creat_ratio"] >= 1.5 and cond.key != "aki":
            secondary.append(DERIVED_DIAGNOSES["AKI"])
        if state["min_na"] < 130:
            secondary.append(DERIVED_DIAGNOSES["HYPONATRAEMIA"])
        if cond.infection and ev.severity > 0.8 and cond.key != "sepsis" and self.rng.random() < 0.3:
            secondary.append(DERIVED_DIAGNOSES["SEPSIS"])
        if state["confused"]:
            secondary.append(DERIVED_DIAGNOSES["DELIRIUM"])
        if state.get("ventilated"):
            secondary.append(DERIVED_DIAGNOSES["RESP_FAILURE"])
        seen = {primary}
        for code in secondary:
            if code not in seen:
                seen.add(code)
                self.add_diagnosis(p, encounter_id, code, "secondary")

    def add_diagnosis(self, p: Patient, encounter_id: str, code: tuple[str, str], position: str) -> None:
        self.rows["Diagnoses"].append({
            "DiagnosisID": self.next_id("DX", 6),
            "EncounterID": encounter_id,
            "LocalPatientIdentifier": p.mrn,
            "ICD10Code": code[0],
            "Description": code[1],
            "Position": position,
        })

    def add_procedure(self, p: Patient, encounter_id: str, key: str, t: datetime) -> None:
        code, description = PROCEDURES[key]
        self.rows["Procedures"].append({
            "ProcedureID": self.next_id("PROC", 6),
            "EncounterID": encounter_id,
            "LocalPatientIdentifier": p.mrn,
            "OPCSCode": code,
            "Description": description,
            "ProcedureDateTime": iso(t),
        })

    # -- run ---------------------------------------------------------------

    def build(self) -> None:
        self.patients = [self.make_patient(i) for i in range(N_PATIENTS)]
        self.schedule = {p.mrn: [] for p in self.patients}
        self.allocate()
        for p in self.patients:
            for event in sorted(self.schedule[p.mrn], key=lambda e: e.start):
                self.simulate(p, event)


def write_database(rows: dict[str, list[dict]]) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        for table in TABLES:
            if not rows[table]:
                continue
            columns = list(rows[table][0])
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
            conn.executemany(sql, [[row[c] for c in columns] for row in rows[table]])
        conn.commit()
    finally:
        conn.close()


def condition_label(code: str, child: bool) -> str:
    if code in CAP_ICD10:
        return "Pneumonia (child)" if child else "Community-acquired pneumonia"
    matches = [c for c in CONDITIONS.values() if c.icd10 == code]
    for cond in matches:  # J45.9 is used for both adult and child asthma
        if (cond.specialties == ("Paediatrics",)) == child:
            return cond.label
    return matches[0].label if matches else code


def print_breakdown(conn: sqlite3.Connection, title: str, sql: str, children: bool | None = None) -> None:
    """children: True = under-16s only, False = adults only, None = everyone."""
    counts: Counter = Counter()
    for code, child, n in conn.execute(sql):
        if children is None or bool(child) == children:
            counts[condition_label(code, bool(child))] += n
    total = sum(counts.values())
    print(f"\n{title} ({total})")
    for label, n in counts.most_common():
        print(f"  {label:<42}{n:>6}{n / total:>7.1%}")


def print_summary(unplaced: Counter) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        print(f"Built {DB_PATH} (seed {SEED}, now = {NOW:%Y-%m-%d %H:%M})\n")
        print(f"{'Table':<16}{'Rows':>8}")
        for table in TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<16}{count:>8}")

        child = "date(e.{dt}) < date(p.DateOfBirth, '+16 years')"
        inpatient_sql = f"""
            SELECT d.ICD10Code, {child.format(dt='AdmissionDateTime')}, COUNT(*)
            FROM Inpatients e
            JOIN Diagnoses d ON d.EncounterID = e.EncounterID AND d.Position = 'primary'
            JOIN Patients p ON p.LocalPatientIdentifier = e.LocalPatientIdentifier
            WHERE e.AdmissionMethod LIKE '{{method}}%' GROUP BY 1, 2"""
        print_breakdown(conn, "Adult emergency admissions", inpatient_sql.format(method="Emergency"), children=False)
        print_breakdown(conn, "Child emergency admissions", inpatient_sql.format(method="Emergency"), children=True)
        print_breakdown(conn, "Elective admissions", inpatient_sql.format(method="Elective"))
        print_breakdown(conn, "ED attendances not admitted", f"""
            SELECT d.ICD10Code, {child.format(dt='CheckInDateTime')}, COUNT(*)
            FROM ECDS e
            JOIN Diagnoses d ON d.EncounterID = e.EncounterID AND d.Position = 'primary'
            JOIN Patients p ON p.LocalPatientIdentifier = e.LocalPatientIdentifier
            WHERE e.DischargeDestination <> 'Admitted' GROUP BY 1, 2""")

        codes = ", ".join(f"'{c}'" for c in CAP_ICD10)
        cap_sql = f"""
            SELECT COUNT(*) FROM Inpatients i
            JOIN Diagnoses d ON d.EncounterID = i.EncounterID AND d.Position = 'primary'
            JOIN Patients p ON p.LocalPatientIdentifier = i.LocalPatientIdentifier
            WHERE d.ICD10Code IN ({codes})
              AND date(i.AdmissionDateTime) >= date(p.DateOfBirth, '+16 years')"""
        since = iso(NOW - timedelta(hours=RECENT_CAP_HOURS))
        cap_total = conn.execute(cap_sql).fetchone()[0]
        cap_recent = conn.execute(cap_sql + f" AND i.AdmissionDateTime >= '{since}'").fetchone()[0]
        cap_current = conn.execute(cap_sql + " AND i.DischargeDateTime IS NULL").fetchone()[0]
        current = conn.execute("SELECT COUNT(*) FROM Inpatients WHERE DischargeDateTime IS NULL").fetchone()[0]

        print("\nDemo checks")
        print(f"  Adult CAP admissions:                 {cap_total:>4}  (need >= {MIN_CAP_ADMISSIONS})")
        print(f"  CAP admitted in last {RECENT_CAP_HOURS}h:             {cap_recent:>4}  (need >= {N_RECENT_CAP})")
        print(f"  CAP currently admitted:               {cap_current:>4}")
        print(f"  Currently admitted (all conditions):  {current:>4}  (target ~{N_CURRENT_INPATIENTS})")
        if unplaced:
            print(f"  Encounters with no suitable patient:  {dict(unplaced)}")
        if cap_total < MIN_CAP_ADMISSIONS or cap_recent < N_RECENT_CAP:
            raise SystemExit("CAP demo guarantees not met")
    finally:
        conn.close()


def main() -> None:
    warehouse = Warehouse(SEED)
    warehouse.build()
    write_database(warehouse.rows)
    print_summary(warehouse.unplaced)


if __name__ == "__main__":
    main()
