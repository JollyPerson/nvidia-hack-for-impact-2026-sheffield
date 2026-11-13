"""Tests for src/store.py and data/seed_studies.py, each on a temporary app.db."""

import sqlite3
from datetime import date, datetime

import pytest

from data.seed_studies import DEMO_STUDIES, seed
from src import store
from src.models import (
    AuditEntry,
    Citation,
    Criterion,
    CriterionAssessment,
    Decision,
    Flag,
    PatientResult,
    ScreeningRun,
    Study,
    Verdict,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    store.init_db(path)
    return path


def make_study(**overrides) -> Study:
    fields = dict(
        title="Test study", condition="Pneumonia", study_design="RCT", intervention="Drug A",
        primary_outcome="Cure", target_sample_size=10, recruitment_start=date(2026, 1, 1),
        recruitment_end=date(2026, 12, 31), chief_investigator="Dr Test", sponsor="Test sponsor",
        ethics_reference="IRAS 1",
        criteria=[
            Criterion(type="inclusion", kind="structured", description="Aged 16+", field="age", operator=">=", value=16),
            Criterion(type="exclusion", kind="notes", description="On home oxygen"),
        ],
    )
    fields.update(overrides)
    return Study(**fields)


def make_result(study_id: str, patient_id: str, approved: bool = False) -> PatientResult:
    citation = Citation(table="ClinicalNotes", record_id="N-000001", field="NoteText", quote="home O2 2L")
    return PatientResult(
        study_id=study_id,
        patient_id=patient_id,
        shortlisted=True,
        assessments=[
            CriterionAssessment(
                criterion_id="C2",
                stage1=Verdict(verdict="met", reasoning="note says home O2", citations=[citation]),
                stage2=Verdict(verdict="insufficient", reasoning="unclear"),
                flags=[Flag(type="stage_disagreement", detail="stage 1 met, stage 2 insufficient")],
            )
        ],
        status="needs_review",
        reasons=["stage disagreement on C2"],
        approved=approved,
    )


def make_run(study_id: str, run_id: str = "R1", patients=None, started=datetime(2026, 11, 13, 9, 0)) -> ScreeningRun:
    return ScreeningRun(
        run_id=run_id, study_id=study_id, status="complete", started_at=started,
        finished_at=datetime(2026, 11, 13, 9, 5), candidates_after_sql=40, shortlisted_stage1=12,
        confirmed_stage2=5, likely_eligible=3, needs_review=1, excluded=1,
        patients=patients if patients is not None else [make_result(study_id, "MRN100001")],
    )


# -- studies -------------------------------------------------------------------


def test_create_study_assigns_ids_and_round_trips(db):
    saved = store.create_study(make_study(), db=db)
    assert saved.id == "S-001"
    assert [c.id for c in saved.criteria] == ["C1", "C2"]
    assert store.get_study("S-001", db=db) == saved
    assert saved.criteria[0].value == 16 and saved.criteria[0].operator.value == ">="

    second = store.create_study(make_study(title="Second"), db=db)
    assert second.id == "S-002"
    assert [s.id for s in store.list_studies(db=db)] == ["S-001", "S-002"]


def test_criteria_keep_their_order_and_given_ids(db):
    crits = [
        Criterion(id="C9", type="exclusion", kind="notes", description="first"),
        Criterion(type="inclusion", kind="notes", description="second"),
    ]
    saved = store.create_study(make_study(criteria=crits), db=db)
    assert [(c.id, c.description) for c in saved.criteria] == [("C9", "first"), ("C1", "second")]


def test_get_missing_study_returns_none(db):
    assert store.get_study("S-999", db=db) is None


def test_duplicate_study_id_is_rejected(db):
    store.create_study(make_study(id="S-010"), db=db)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_study(make_study(id="S-010"), db=db)


# -- runs ------------------------------------------------------------------------


def test_run_round_trips_with_nested_verdicts(db):
    study = store.create_study(make_study(), db=db)
    run = make_run(study.id)
    assert store.save_run(run, db=db) == run
    assert store.get_run("R1", db=db) == run
    assert store.get_run("missing", db=db) is None


def test_save_run_updates_a_running_run(db):
    study = store.create_study(make_study(), db=db)
    running = ScreeningRun(run_id="R1", study_id=study.id, started_at=datetime(2026, 11, 13, 9, 0))
    store.save_run(running, db=db)
    assert store.get_run("R1", db=db).status == "running"

    store.save_run(make_run(study.id), db=db)
    stored = store.get_run("R1", db=db)
    assert stored.status == "complete" and stored.likely_eligible == 3 and len(stored.patients) == 1


def test_latest_run_is_the_most_recent(db):
    study = store.create_study(make_study(), db=db)
    store.save_run(make_run(study.id, "R1", started=datetime(2026, 11, 12, 9, 0)), db=db)
    store.save_run(make_run(study.id, "R2", started=datetime(2026, 11, 13, 9, 0)), db=db)
    assert store.get_latest_run(study.id, db=db).run_id == "R2"
    assert store.get_latest_run("S-999", db=db) is None


# -- decisions -------------------------------------------------------------------


def decision(value: str, minute: int) -> Decision:
    return Decision(patient_id="MRN100001", run_id="R1", decision=value, note="", who="Dr Test",
                    when=datetime(2026, 11, 13, 10, minute))


def test_decision_sets_approved_and_latest_wins(db):
    study = store.create_study(make_study(), db=db)
    store.save_run(make_run(study.id), db=db)

    store.record_decision(decision("approved", 0), db=db)
    assert store.get_run("R1", db=db).patients[0].approved is True

    store.record_decision(decision("rejected", 5), db=db)
    assert store.get_run("R1", db=db).patients[0].approved is False
    assert store.get_decisions("R1", db=db)["MRN100001"].decision == "rejected"


def test_resaving_results_keeps_the_clinicians_approval(db):
    study = store.create_study(make_study(), db=db)
    store.save_run(make_run(study.id), db=db)
    store.record_decision(decision("approved", 0), db=db)

    store.save_run(make_run(study.id), db=db)  # pipeline result says approved=False
    assert store.get_run("R1", db=db).patients[0].approved is True


def test_decision_for_patient_not_in_run_is_rejected(db):
    study = store.create_study(make_study(), db=db)
    store.save_run(make_run(study.id), db=db)
    with pytest.raises(sqlite3.IntegrityError):
        store.record_decision(Decision(patient_id="MRN999999", run_id="R1", decision="approved",
                                       who="Dr Test", when=datetime(2026, 11, 13, 10, 0)), db=db)


# -- audit log -------------------------------------------------------------------


def test_audit_log_appends_and_filters(db):
    for study_id, action in [("S-001", "study_created"), ("S-002", "study_created"), ("S-001", "run_started")]:
        store.append_audit(AuditEntry(when=datetime(2026, 11, 13, 9, 0), study_id=study_id, who="Dr Test",
                                      action=action), db=db)
    assert [e.action for e in store.read_audit("S-001", db=db)] == ["study_created", "run_started"]
    assert len(store.read_audit(db=db)) == 3


def test_audit_log_cannot_be_changed(db):
    store.append_audit(AuditEntry(when=datetime(2026, 11, 13), study_id="S-001", who="x", action="a"), db=db)
    conn = sqlite3.connect(db)
    for sql in ("UPDATE AuditLog SET note = 'edited'", "DELETE FROM AuditLog"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
    conn.close()


# -- rules -----------------------------------------------------------------------


def test_app_db_has_no_patient_detail_columns(db):
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]  # skip SQLite's own
    columns = {c[1].lower() for t in tables for c in conn.execute(f"PRAGMA table_info({t})")}
    conn.close()
    forbidden = {"age", "sex", "dateofbirth", "dob", "name", "mobile", "mobilenumber", "landline",
                 "email", "guardianname", "guardianphone", "postcodedistrict", "ethniccategory"}
    assert not columns & forbidden


def test_seed_adds_both_demo_studies_once(tmp_path):
    path = tmp_path / "app.db"
    assert seed(path) == ["S-001", "S-002"]
    assert seed(path) == []  # second run adds nothing
    studies = store.list_studies(db=path)
    assert studies == DEMO_STUDIES
    assert {s.condition for s in studies} == {"Community-acquired pneumonia", "Acute heart failure"}
    assert len(store.read_audit(db=path)) == 2
