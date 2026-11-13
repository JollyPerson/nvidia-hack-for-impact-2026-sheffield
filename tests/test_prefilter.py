"""Prefilter tests: every field type on a small in-memory warehouse with known answers,
parameterisation, criterion checking, and the pneumonia demo study on data/warehouse.db."""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

import config
from data.seed_studies import HEART_FAILURE, PNEUMONIA
from src.models import Criterion, Study
from src.prefilter import PrefilterError, connect_readonly, run_prefilter

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 13, 9, 0)

# ---------------------------------------------------------------------------
# A tiny warehouse (NOW = 2026-11-13 09:00)
#
# P1  76y  ED-01 -> IP-01, admitted 12/11 15:00, still in. CAP (J18.9) + COPD (J44.9).
#          First obs (in ED): RR 32, SpO2 90 air, T 38.5, BP 85/50, HR 120, confused
#          -> CURB-65 5 with urea 9, NEWS2 15. Later ward obs BP 120/70.
#          eGFR 25 on 12/11. CPAP (E85.2) on 12/11.
# P2  14y  IP-02 emergency, 10/11 12:00 - 12/11 12:00, CAP, no ED attendance, normal obs.
# P3  46y  IP-03 elective THR (M16.1, W37.1) 01/11 - 03/11, eGFR 80.
#          IP-04 emergency AKI (N17.9) in January 2026 with eGFR 20 (outside 90 days).
# P4  86y  IP-05 old CAP (J18.1) Jan 2025. IP-06 heart failure (I50.0) admitted
#          13/11 06:00, still in: first BP 88/60, NT-proBNP 5000, urea 8.
# ---------------------------------------------------------------------------


def build_warehouse() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "data" / "schema.sql").read_text())

    def add(table, **row):
        conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))

    for mrn, dob, sex in [("P1", "1950-01-01", "Male"), ("P2", "2012-06-01", "Female"),
                          ("P3", "1980-03-15", "Female"), ("P4", "1940-05-05", "Male")]:
        add("Patients", LocalPatientIdentifier=mrn, DateOfBirth=dob, Sex=sex)

    def admission(ip, mrn, admitted, discharged, method="Emergency - via ED", ed=None, checkin=None):
        if ed:
            add("ECDS", EncounterID=ed, LocalPatientIdentifier=mrn, CheckInDateTime=checkin, Acuity=2,
                ChiefComplaint="SOB", DischargeDestination="Admitted")
        add("Inpatients", EncounterID=ip, LocalPatientIdentifier=mrn, AdmissionDateTime=admitted,
            DischargeDateTime=discharged, AdmissionMethod=method, Specialty="Acute Medicine", Ward="AMU")

    def obs(oid, enc, mrn, when, rr=16, spo2=97, o2=0, temp=37.0, sbp=125, dbp=75, hr=75, acvpu="A"):
        add("Observations", ObservationID=oid, EncounterID=enc, LocalPatientIdentifier=mrn, RecordedDateTime=when,
            RespRate=rr, SpO2=spo2, OnOxygen=o2, Temperature=temp, SystolicBP=sbp, DiastolicBP=dbp,
            HeartRate=hr, Consciousness=acvpu)

    def lab(pid, enc, mrn, code, value, when):
        add("Pathology", PathologyID=pid, EncounterID=enc, LocalPatientIdentifier=mrn, TestCode=code,
            TestName=code, Value=value, Units="", CollectedDateTime=when)

    def dx(did, enc, mrn, code, position="primary"):
        add("Diagnoses", DiagnosisID=did, EncounterID=enc, LocalPatientIdentifier=mrn, ICD10Code=code,
            Description=code, Position=position)

    def proc(pid, enc, mrn, code, when):
        add("Procedures", ProcedureID=pid, EncounterID=enc, LocalPatientIdentifier=mrn, OPCSCode=code,
            Description=code, ProcedureDateTime=when)

    # P1
    admission("IP-01", "P1", "2026-11-12T15:00:00", None, ed="ED-01", checkin="2026-11-12T10:00:00")
    obs("OBS-01", "ED-01", "P1", "2026-11-12T10:10:00", rr=32, spo2=90, temp=38.5, sbp=85, dbp=50, hr=120, acvpu="C")
    obs("OBS-02", "IP-01", "P1", "2026-11-12T20:00:00", sbp=120, dbp=70)
    lab("PATH-01", "ED-01", "P1", "UREA", 9.0, "2026-11-12T10:30:00")
    lab("PATH-02", "ED-01", "P1", "EGFR", 25.0, "2026-11-12T10:30:00")
    lab("PATH-03", "ED-01", "P1", "NTBNP", 500.0, "2026-11-12T10:30:00")
    dx("DX-01", "IP-01", "P1", "J18.9")
    dx("DX-02", "IP-01", "P1", "J44.9", "secondary")
    proc("PROC-01", "IP-01", "P1", "E85.2", "2026-11-12T18:00:00")
    # P2
    admission("IP-02", "P2", "2026-11-10T12:00:00", "2026-11-12T12:00:00")
    obs("OBS-03", "IP-02", "P2", "2026-11-10T12:30:00")
    lab("PATH-04", "IP-02", "P2", "UREA", 4.0, "2026-11-11T07:00:00")
    dx("DX-03", "IP-02", "P2", "J18.9")
    # P3
    admission("IP-04", "P3", "2026-01-01T12:00:00", "2026-01-05T12:00:00")
    lab("PATH-05", "IP-04", "P3", "EGFR", 20.0, "2026-01-01T13:00:00")
    dx("DX-04", "IP-04", "P3", "N17.9")
    admission("IP-03", "P3", "2026-11-01T08:00:00", "2026-11-03T10:00:00", method="Elective - waiting list")
    obs("OBS-04", "IP-03", "P3", "2026-11-01T08:30:00")
    lab("PATH-06", "IP-03", "P3", "EGFR", 80.0, "2026-11-01T09:00:00")
    lab("PATH-07", "IP-03", "P3", "UREA", 5.0, "2026-11-01T09:00:00")
    dx("DX-05", "IP-03", "P3", "M16.1")
    proc("PROC-02", "IP-03", "P3", "W37.1", "2026-11-01T10:00:00")
    # P4
    admission("IP-05", "P4", "2025-01-10T12:00:00", "2025-01-15T12:00:00")
    obs("OBS-05", "IP-05", "P4", "2025-01-10T12:30:00")
    dx("DX-06", "IP-05", "P4", "J18.1")
    admission("IP-06", "P4", "2026-11-13T06:00:00", None)
    obs("OBS-06", "IP-06", "P4", "2026-11-13T06:30:00", spo2=95, temp=36.8, sbp=88, dbp=60, hr=90)
    obs("OBS-07", "IP-06", "P4", "2026-11-13T08:30:00", sbp=110, dbp=70)
    lab("PATH-08", "IP-06", "P4", "NTBNP", 5000.0, "2026-11-13T07:00:00")
    lab("PATH-09", "IP-06", "P4", "UREA", 8.0, "2026-11-13T07:00:00")
    dx("DX-07", "IP-06", "P4", "I50.0")
    conn.commit()
    return conn


@pytest.fixture
def wh():
    conn = build_warehouse()
    yield conn
    conn.close()


def study(*criteria: Criterion) -> Study:
    return Study(id="S-T", title="Test", condition="Test", study_design="RCT", intervention="x",
                 primary_outcome="y", target_sample_size=10, recruitment_start=date(2026, 1, 1),
                 recruitment_end=date(2027, 1, 1), chief_investigator="Dr T", sponsor="T", ethics_reference="T",
                 criteria=list(criteria))


def inc(field, operator, value, window=None, cid="C1"):
    return Criterion(id=cid, type="inclusion", kind="structured", description=f"{field} {operator} {value}",
                     field=field, operator=operator, value=value, time_window_days=window)


def exc(field, operator, value, window=None, cid="C1"):
    return Criterion(id=cid, type="exclusion", kind="structured", description=f"not {field} {operator} {value}",
                     field=field, operator=operator, value=value, time_window_days=window)


def run(conn, *criteria):
    return run_prefilter(study(*criteria), conn=conn, now=NOW)


def picked(result) -> dict[str, str]:
    """patient id -> qualifying encounter id"""
    return {c.patient_id: c.encounter_id for c in result.candidates}


# -- each field ------------------------------------------------------------------


def test_reference_time_comes_from_config():
    assert config.REFERENCE_NOW == NOW


def test_age(wh):
    result = run(wh, inc("age", ">=", 16))
    assert picked(result) == {"P1": "IP-01", "P3": "IP-03", "P4": "IP-06"}
    assert result.patients_considered == 4
    assert result.removed_by_criterion == {"C1": 1}
    cite = result.candidates[0].citations["C1"][0]
    assert (cite.table, cite.record_id, cite.field, cite.quote) == ("Patients", "P1", "DateOfBirth", "1950-01-01")


def test_primary_diagnosis_prefix(wh):
    assert picked(run(wh, inc("primary_diagnosis", "==", "J18"))) == {"P1": "IP-01", "P2": "IP-02", "P4": "IP-05"}
    assert picked(run(wh, inc("primary_diagnosis", "==", "J18.1"))) == {"P4": "IP-05"}
    assert picked(run(wh, inc("primary_diagnosis", "!=", "J18"))) == {"P3": "IP-03", "P4": "IP-06"}
    cite = run(wh, inc("primary_diagnosis", "==", "j18")).candidates[0].citations["C1"][0]  # case-insensitive
    assert (cite.table, cite.record_id, cite.quote) == ("Diagnoses", "DX-01", "J18.9")


def test_admission_method(wh):
    assert picked(run(wh, inc("admission_method", "==", "elective"))) == {"P3": "IP-03"}
    assert picked(run(wh, inc("admission_method", "==", "Emergency"))) == {
        "P1": "IP-01", "P2": "IP-02", "P3": "IP-04", "P4": "IP-06"}


def test_admitted_within_hours(wh):
    result = run(wh, inc("admitted_within_hours", "<=", 48))
    assert picked(result) == {"P1": "IP-01", "P4": "IP-06"}  # IP-02 was 69 hours ago
    cite = result.candidates[0].citations["C1"][0]
    assert (cite.table, cite.field, cite.quote) == ("Inpatients", "AdmissionDateTime", "2026-11-12T15:00:00")


def test_currently_admitted(wh):
    assert picked(run(wh, inc("currently_admitted", "==", "yes"))) == {"P1": "IP-01", "P4": "IP-06"}
    assert picked(run(wh, inc("currently_admitted", "==", "no"))) == {"P2": "IP-02", "P3": "IP-03", "P4": "IP-05"}


def test_curb65_uses_first_obs_urea_and_age(wh):
    result = run(wh, inc("curb65", ">=", 2))
    # P1: confusion + urea 9 + RR 32 + BP 85/50 + 76y = 5. P4: urea 8 + diastolic 60 + 86y = 3.
    # P2 (14y, urea 4) and P3 (urea 5) score 0. IP-05 has no urea, so it cannot be scored.
    assert picked(result) == {"P1": "IP-01", "P4": "IP-06"}
    cites = {(c.table, c.record_id, c.field, c.quote) for c in result.candidates[0].citations["C1"]}
    assert cites == {("Observations", "OBS-01", "CURB65", "5"), ("Pathology", "PATH-01", "Value", "9"),
                     ("Patients", "P1", "DateOfBirth", "1950-01-01")}


def test_news2_from_admission_obs(wh):
    result = run(wh, inc("news2", ">=", 7))
    assert picked(result) == {"P1": "IP-01"}  # 15; P4's first set scores 4
    cite = result.candidates[0].citations["C1"][0]
    assert (cite.record_id, cite.field, cite.quote) == ("OBS-01", "NEWS2", "15")


def test_lab_with_time_window(wh):
    result = run(wh, exc("egfr", "<", 30, window=90))
    assert picked(result) == {"P2": "IP-02", "P3": "IP-03", "P4": "IP-06"}  # P3's eGFR 20 was in January
    assert result.removed_by_criterion == {"C1": 1}
    assert picked(run(wh, exc("egfr", "<", 30, window=365))).keys() == {"P2", "P4"}


def test_lab_without_window_uses_admission_result(wh):
    result = run(wh, inc("ntprobnp", ">=", 2000))
    assert picked(result) == {"P4": "IP-06"}
    cite = result.candidates[0].citations["C1"][0]
    assert (cite.table, cite.record_id, cite.quote) == ("Pathology", "PATH-08", "5000")
    assert picked(run(wh, inc("urea", ">", 7))) == {"P1": "IP-01", "P4": "IP-06"}
    assert picked(run(wh, inc("crp", ">", 0))) == {}  # no CRP results at all


def test_systolic_bp(wh):
    assert picked(run(wh, inc("systolic_bp", "<", 90))) == {"P1": "IP-01", "P4": "IP-06"}
    # Admission value only: P1's later 120 does not count without a window
    assert "P1" not in picked(run(wh, inc("systolic_bp", ">", 100)))
    # With a window any reading counts: P1's ward reading of 120 is within 1 day
    assert "P1" in picked(run(wh, inc("systolic_bp", ">", 100, window=1)))


def test_diagnosis_code_any_position(wh):
    result = run(wh, inc("diagnosis_code", "==", "J44"))
    assert picked(result) == {"P1": "IP-01"}  # J44.9 is a secondary diagnosis
    assert result.candidates[0].citations["C1"][0].record_id == "DX-02"
    assert "P1" not in picked(run(wh, exc("diagnosis_code", "==", "J44")))
    assert picked(run(wh, inc("diagnosis_code", "!=", "J44"))).keys() == {"P2", "P3", "P4"}


def test_procedure_code_with_and_without_window(wh):
    assert picked(run(wh, inc("procedure_code", "==", "E85"))) == {"P1": "IP-01"}
    assert picked(run(wh, inc("procedure_code", "==", "W37", window=5))) == {}  # THR was 12 days ago
    assert picked(run(wh, inc("procedure_code", "==", "W37", window=30))) == {"P3": "IP-03"}


# -- combining criteria ------------------------------------------------------------


def test_criteria_apply_in_order_and_count_removals(wh):
    notes = Criterion(id="C9", type="inclusion", kind="notes", description="LLM decides this one")
    result = run(wh,
                 inc("age", ">=", 16, cid="C1"),
                 notes,
                 inc("currently_admitted", "==", "yes", cid="C2"),
                 exc("egfr", "<", 30, window=90, cid="C3"))
    assert result.removed_by_criterion == {"C1": 1, "C2": 1, "C3": 1}  # P2, then P3, then P1
    assert picked(result) == {"P4": "IP-06"}
    assert set(result.candidates[0].citations) == {"C1", "C2"}  # met inclusions; notes criteria untouched


def test_patient_kept_if_any_episode_passes(wh):
    # P4's current admission fails the CAP code but the 2025 episode passes it
    result = run(wh, inc("primary_diagnosis", "==", "J18"), inc("age", ">=", 80, cid="C2"))
    assert picked(result) == {"P4": "IP-05"}


# -- checking criteria -------------------------------------------------------------


@pytest.mark.parametrize("criterion, message", [
    (inc("bmi", ">", 30), "unsupported field 'bmi'"),
    (inc("age", ">=", "old"), "needs a number"),
    (inc("primary_diagnosis", ">", "J18"), "operator '>' is not allowed"),
    (inc("age", ">=", 16, window=30), "does not take a time window"),
    (inc("admission_method", "==", "urgent"), "must be 'emergency' or 'elective'"),
    (inc("currently_admitted", "==", "maybe"), "needs yes or no"),
])
def test_bad_criteria_raise_clear_errors(wh, criterion, message):
    with pytest.raises(PrefilterError, match=message):
        run(wh, criterion)


def test_bad_criterion_fails_before_touching_the_database():
    with pytest.raises(PrefilterError):
        run_prefilter(study(inc("bmi", ">", 30)), conn=None)  # would otherwise open the warehouse


# -- parameterisation ----------------------------------------------------------------

INJECTIONS = [
    "J18' OR '1'='1",
    "x'); DROP TABLE Patients; --",
    "%",                      # would match everything if it reached a LIKE
    "_18%",
    "J18\" OR 1=1 --",
]


@pytest.mark.parametrize("value", INJECTIONS)
@pytest.mark.parametrize("field", ["primary_diagnosis", "diagnosis_code", "procedure_code"])
def test_sql_in_a_value_is_just_text(wh, field, value):
    assert run(wh, inc(field, "==", value)).candidates == []
    # As an exclusion it removes no one, and every table is intact
    assert len(run(wh, exc(field, "==", value)).candidates) == 4
    assert wh.execute("SELECT COUNT(*) FROM Patients").fetchone()[0] == 4


def test_sql_in_admission_method_is_rejected(wh):
    with pytest.raises(PrefilterError):
        run(wh, inc("admission_method", "==", "emergency' OR '1'='1"))


# -- the pneumonia demo study on the generated warehouse -------------------------------


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="data/warehouse.db not built")
@pytest.mark.parametrize("demo", [PNEUMONIA, HEART_FAILURE], ids=["pneumonia", "heart_failure"])
def test_demo_study_on_warehouse(demo):
    result = run_prefilter(demo)
    structured = [c.id for c in demo.criteria if c.kind == "structured"]
    print(f"\n{demo.short_title}: {len(result.candidates)} candidates from "
          f"{result.patients_considered} admitted patients (reference {result.reference_time:%Y-%m-%d %H:%M})")
    for key, n in result.removed_by_criterion.items():
        crit = next(c for c in demo.criteria if c.id == key)
        print(f"  removed by {key} ({crit.type.value}: {crit.description}): {n}")

    assert list(result.removed_by_criterion) == structured
    assert result.patients_considered == len(result.candidates) + sum(result.removed_by_criterion.values())
    conn = connect_readonly()
    # Both demo studies find someone (HOMESTEP-HF's NT-proBNP window is 90 days: the generator's latest
    # NT-proBNP results are from late October, so the original 7-day window removed everyone).
    assert result.candidates, f"expected {demo.short_title} candidates"

    # Every citation points at a real row for that patient and quotes what is stored there
    lookup = {"Patients": ("LocalPatientIdentifier", None), "Pathology": ("PathologyID", "Value"),
              "Observations": ("ObservationID", None), "Inpatients": ("EncounterID", None),
              "Diagnoses": ("DiagnosisID", "ICD10Code"), "Procedures": ("ProcedureID", "OPCSCode")}
    for cand in result.candidates:
        inclusions = {c.id for c in demo.criteria if c.kind == "structured" and c.type == "inclusion"}
        assert set(cand.citations) == inclusions
        for cites in cand.citations.values():
            for cite in cites:
                id_col, value_col = lookup[cite.table]
                row = conn.execute(f"SELECT * FROM {cite.table} WHERE {id_col} = ?", (cite.record_id,)).fetchone()
                assert row is not None and row["LocalPatientIdentifier"] == cand.patient_id
                if value_col == "Value":
                    assert float(cite.quote) == row["Value"]
                elif cite.table == "Patients":
                    assert cite.quote == row["DateOfBirth"]
    conn.close()
