"""Tests for src/validation.py, using a small in-memory warehouse built from data/schema.sql."""

import sqlite3
from pathlib import Path

import pytest

from src.models import (
    Citation,
    CriterionAssessment,
    FlagType,
    PatientResult,
    Verdict,
)
from src.validation import check_citation, check_citations, validate_patient

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA.read_text())
    db.executescript(
        """
        INSERT INTO Patients VALUES ('P1', '1950-03-02', 'F', 'A', 'S10');
        INSERT INTO Patients VALUES ('P2', '1962-07-19', 'M', 'A', 'S11');

        INSERT INTO Inpatients VALUES ('E1', 'P1', '2026-10-01T08:00:00', NULL, 'Emergency', 'Respiratory', 'Ward 5');
        INSERT INTO Inpatients VALUES ('E2', 'P2', '2026-10-03T09:00:00', NULL, 'Emergency', 'Respiratory', 'Ward 6');

        INSERT INTO ClinicalNotes VALUES ('N1', 'E1', 'P1', 'clerking', 'FY1', '2026-10-01T09:00:00',
            'COPD on home O2 2L via concentrator.
             No hx of MI.');
        INSERT INTO ClinicalNotes VALUES ('N2', 'E2', 'P2', 'clerking', 'FY1', '2026-10-03T10:00:00',
            'Known T2DM. Lives alone.');

        INSERT INTO Observations (ObservationID, EncounterID, LocalPatientIdentifier, RecordedDateTime,
            RespRate, SpO2, OnOxygen, HeartRate)
            VALUES ('O1', 'E1', 'P1', '2026-10-01T08:30:00', 24, 92, 1, 104);

        INSERT INTO Pathology VALUES ('L1', 'E1', 'P1', 'CRP', 'C-reactive protein', 145.0, 'mg/L', 0, 5,
            '2026-10-01T08:40:00');

        INSERT INTO Diagnoses VALUES ('D1', 'E1', 'P1', 'J18.9', 'Pneumonia, unspecified', 'primary');

        -- A full obs set and a urea, for recalculating scores. Worked out by hand:
        -- CURB-65 = 5: confused (C), urea 9.1 > 7, RR 32 >= 30, SBP 88 < 90, age 76 >= 65.
        -- NEWS2 = 15: RR 32 (3), SpO2 91 (3, scale 1: <=91), air (0), SBP 88 (3), HR 112 (2),
        --             new confusion (3), T 38.4 (1).
        INSERT INTO Observations VALUES ('O2', 'E1', 'P1', '2026-10-01T08:15:00', 32, 91, 0, 38.4, 88, 55, 112, 'C');
        INSERT INTO Pathology VALUES ('L2', 'E1', 'P1', 'UREA', 'Urea', 9.1, 'mmol/L', 2.5, 7.8, '2026-10-01T08:40:00');
        """
    )
    yield db
    db.close()


def cite(table, record_id, field, quote):
    return Citation(table=table, record_id=record_id, field=field, quote=quote)


# --- check_citation: genuine evidence passes ---------------------------------


@pytest.mark.parametrize(
    "citation",
    [
        cite("ClinicalNotes", "N1", "NoteText", "home O2 2L via concentrator"),
        cite("ClinicalNotes", "N1", "NoteText", "HOME   o2 2l\nvia CONCENTRATOR"),  # case and spacing ignored
        cite("ClinicalNotes", "N1", "NoteText", "concentrator. No hx of MI"),  # spans a line break
        cite("Observations", "O1", "SpO2", "SpO2 92%"),
        cite("Observations", "O1", "SpO2", "92"),
        cite("Pathology", "L1", "Value", "CRP 145 mg/L"),
        cite("Diagnoses", "D1", "Description", "pneumonia"),
        cite("Patients", "P1", "DateOfBirth", "1950-03-02"),
        # How real models write citations (matches src/tools.py and the screening prompts)
        cite("clinicalnotes", "N1", "NoteText", "home O2 2L"),  # table name in lower case
        cite("ClinicalNote", "N1", "notetext", "home O2 2L"),  # singular table, field in lower case
        cite("ClinicalNotes", "N1", "NoteText", "…COPD on home O2 2L"),  # '…' from a shortened note
        cite("ClinicalNotes", "N1", "NoteText", "\"No hx of MI.\""),  # wrapped in quote marks
        # Seen with the real models on the real warehouse: two real passages joined with "..."
        cite("ClinicalNotes", "N1", "NoteText", "COPD on home O2 2L... No hx of MI."),
        cite("ClinicalNotes", "N1", "NoteText", "COPD on home O2 … via concentrator"),
        # Notes reach the models as JSON, so a line break can be copied as the characters \n
        cite("ClinicalNotes", "N1", "NoteText", "via concentrator.\\nNo hx of MI"),
    ],
)
def test_genuine_citation_passes(conn, citation):
    assert check_citation(conn, citation, "P1") is None


# --- check_citation: invented or wrong evidence fails ------------------------


@pytest.mark.parametrize(
    "citation, reason",
    [
        (cite("ClinicalNotes", "N999", "NoteText", "home O2"), "record does not exist"),
        (cite("ClinicalNotes", "N2", "NoteText", "Known T2DM"), "record belongs to a different patient"),
        (cite("ClinicalNotes", "N1", "NoteText", "home O2 3L via concentrator"), "quote does not match the record"),
        (cite("ClinicalNotes", "N1", "NoteText", "no"), "quote does not match the record"),
        (cite("Observations", "O1", "SpO2", "SpO2 88%"), "quote does not match the record"),
        (cite("Observations", "O1", "Temperature", "37.2"), "field is missing or empty"),
        (cite("Observations", "O1", "MadeUpField", "1"), "field is missing or empty"),
        (cite("PatientContacts", "P1", "MobileNumber", "07700 900123"), "this table cannot be cited"),
        (cite("patientcontacts", "P1", "MobileNumber", "07700 900123"), "this table cannot be cited"),
        (cite("ClinicalNotes", "N1", "NoteText", "…"), "quote does not match the record"),
        # "..." quotes: every part must be real, in this same note
        (cite("ClinicalNotes", "N1", "NoteText", "home O2 3L... No hx of MI"), "quote does not match the record"),
        (cite("ClinicalNotes", "N1", "NoteText", "No new confusion... home O2 2L"), "quote does not match the record"),
        (cite("ClinicalNotes", "N1", "NoteText", "home O2 2L... no"), "quote does not match the record"),
        (cite("ClinicalNotes", "N1", "NoteText", "home O2 2L... Known T2DM"), "quote does not match the record"),  # P2's note
        (cite("Medications", "M1", "Drug", "salbutamol"), "this table cannot be cited"),
    ],
)
def test_bad_citation_fails(conn, citation, reason):
    assert check_citation(conn, citation, "P1") == reason


# --- prefilter evidence (src/prefilter.py): scores, empty fields, short codes --


CURB65_BUNDLE = [  # the three citations the prefilter gives for a CURB-65 criterion
    cite("Observations", "O2", "CURB65", "5"),
    cite("Pathology", "L2", "Value", "9.1"),
    cite("Patients", "P1", "DateOfBirth", "1950-03-02"),
]


def test_prefilter_curb65_bundle_passes(conn):
    assert check_citations(conn, CURB65_BUNDLE, "P1") == []


@pytest.mark.parametrize(
    "citation, siblings, reason",
    [
        (cite("Observations", "O2", "CURB65", "3"), CURB65_BUNDLE, "quote does not match the recalculated score"),
        (cite("Observations", "O2", "CURB65", "5"), [], "score cannot be recalculated from the records"),
        # the "urea" next to it is really a CRP result, so the score can't be checked
        (cite("Observations", "O2", "CURB65", "5"), [cite("Pathology", "L1", "Value", "145")],
         "score cannot be recalculated from the records"),
        (cite("Observations", "O1", "NEWS2", "5"), [], "score cannot be recalculated from the records"),  # no temp
        (cite("Observations", "O2", "NEWS2", "9"), [], "quote does not match the recalculated score"),
    ],
)
def test_wrong_or_unprovable_score_fails(conn, citation, siblings, reason):
    assert check_citation(conn, citation, "P1", siblings=siblings) == reason


def test_news2_is_recalculated(conn):
    assert check_citation(conn, cite("Observations", "O2", "NEWS2", "15"), "P1") is None


def test_empty_quote_for_empty_field(conn):
    # The prefilter cites "" for a NULL DischargeDateTime (patient not yet discharged).
    assert check_citation(conn, cite("Inpatients", "E1", "DischargeDateTime", ""), "P1") is None
    assert check_citation(conn, cite("Inpatients", "E1", "DischargeDateTime", "2026-10-05"), "P1") == (
        "field is missing or empty"
    )


def test_short_exact_values(conn):
    assert check_citation(conn, cite("Patients", "P1", "Sex", "F"), "P1") is None
    assert check_citation(conn, cite("Patients", "P1", "Sex", "M"), "P1") == "quote does not match the record"


def test_rounded_number_passes(conn):
    # prefilter writes values with f"{v:g}"; 145.0 becomes "145"
    assert check_citation(conn, cite("Pathology", "L1", "Value", "145"), "P1") is None


# --- validate_patient: flags, never removes ----------------------------------


def verdict(value, *citations):
    return Verdict(verdict=value, reasoning="test", citations=list(citations))


def test_valid_patient_gets_no_flags(conn):
    good = cite("ClinicalNotes", "N1", "NoteText", "home O2 2L")
    result = PatientResult(
        study_id="S1",
        patient_id="P1",
        assessments=[CriterionAssessment(criterion_id="C1", stage1=verdict("met", good), stage2=verdict("met", good))],
    )
    out = validate_patient(conn, result)
    assert out.assessments[0].flags == []


def test_bad_citation_is_flagged_with_its_stage(conn):
    good = cite("ClinicalNotes", "N1", "NoteText", "home O2 2L")
    fake = cite("ClinicalNotes", "N999", "NoteText", "home O2 2L")
    result = PatientResult(
        study_id="S1",
        patient_id="P1",
        assessments=[CriterionAssessment(criterion_id="C1", stage1=verdict("met", good), stage2=verdict("met", fake))],
    )
    flags = validate_patient(conn, result).assessments[0].flags
    assert len(flags) == 1
    assert flags[0].type == FlagType.citation_failed
    assert flags[0].stage == 2
    assert "N999" in flags[0].detail


def test_disagreement_is_flagged(conn):
    good = cite("ClinicalNotes", "N1", "NoteText", "No hx of MI")
    result = PatientResult(
        study_id="S1",
        patient_id="P1",
        assessments=[
            CriterionAssessment(criterion_id="C1", stage1=verdict("met", good), stage2=verdict("not_met", good))
        ],
    )
    flags = validate_patient(conn, result).assessments[0].flags
    assert [f.type for f in flags] == [FlagType.stage_disagreement]


def test_stage1_insufficient_then_stage2_answer_is_not_a_disagreement(conn):
    # Seen with the real models on the five test patients: the screener passes an
    # uncertain criterion on, and the verifier settles it. That is the design, not a conflict.
    good = cite("ClinicalNotes", "N1", "NoteText", "No hx of MI")
    result = PatientResult(
        study_id="S1",
        patient_id="P1",
        assessments=[
            CriterionAssessment(criterion_id="C1", stage1=verdict("insufficient", good), stage2=verdict("not_met", good))
        ],
    )
    assert validate_patient(conn, result).assessments[0].flags == []


def test_empty_search_result_cited_as_evidence_is_caught(conn):
    # Seen with the real models: the verifier cited an empty tool result as a "record".
    fake = cite("Diagnoses", "N/A", "rows", "[]")
    assert check_citation(conn, fake, "P1") == "record does not exist"


def test_not_shortlisted_and_insufficient_are_handled(conn):
    # Stage 2 never ran (not shortlisted), and an `insufficient` verdict has no citations.
    result = PatientResult(
        study_id="S1",
        patient_id="P1",
        assessments=[CriterionAssessment(criterion_id="C1", stage1=verdict("insufficient"), stage2=None)],
    )
    assert validate_patient(conn, result).assessments[0].flags == []


def test_nothing_is_removed(conn):
    fake = cite("ClinicalNotes", "N2", "NoteText", "Known T2DM")  # another patient's note
    result = PatientResult(
        study_id="S1",
        patient_id="P1",
        assessments=[
            CriterionAssessment(criterion_id="C1", stage1=verdict("met", fake)),
            CriterionAssessment(criterion_id="C2", stage1=verdict("insufficient")),
        ],
    )
    out = validate_patient(conn, result)
    assert out.patient_id == "P1"
    assert [a.criterion_id for a in out.assessments] == ["C1", "C2"]
    assert out.assessments[0].stage1.verdict.value == "met"  # verdict untouched, only flagged
    assert out.assessments[0].flags[0].type == FlagType.citation_failed
