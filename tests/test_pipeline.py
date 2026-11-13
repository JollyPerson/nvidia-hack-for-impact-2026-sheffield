"""Tests for src/pipeline.py: the five hand-built patients through every stage, with a fake model."""

from data.seed_studies import PNEUMONIA
from scripts.five_patients import STUDY, build_db
from src.llm import LLM
from src.models import FinalStatus
from src.pipeline import STEPS, run_screening
from tests.fake_llm import RuleBasedClient


def test_five_patients_through_the_pipeline():
    steps = []
    run = run_screening(STUDY, LLM(RuleBasedClient()), build_db(), on_step=steps.append, run_id="r1")
    status = {p.patient_id: p.status for p in run.patients}
    assert status["P001"] == FinalStatus.likely_eligible  # clear match
    assert status["P002"] == FinalStatus.excluded  # COPD on home O2, dropped at stage 1
    assert status["P003"] == FinalStatus.needs_review  # ambiguous X-ray
    assert status["P005"] == FinalStatus.needs_review  # nothing in the records
    assert run.status == "complete" and run.finished_at
    assert (run.candidates_after_sql, run.shortlisted_stage1) == (5, 4)
    assert run.likely_eligible + run.needs_review + run.excluded == 5
    # Every step reports running then done, in order, with timings for the done events.
    done = [s for s in steps if s["state"] == "done"]
    assert [s["name"] for s in done] == STEPS and all("seconds" in s for s in done)


def test_max_candidates_limits_the_run():
    run = run_screening(STUDY, LLM(RuleBasedClient()), build_db(), max_candidates=2)
    assert run.candidates_after_sql == 2 and len(run.patients) == 2
    # Same admission time for all five, so the newest-first order falls back to patient id (descending).
    assert [p.patient_id for p in run.patients] == ["P005", "P004"]


def test_structured_inclusions_are_code_verdicts_and_skip_validation(tmp_path):
    """The prefilter's evidence is added as stage-1 `met` verdicts, without a stage 2 and without flags."""
    conn = build_db()
    conn.execute("UPDATE Patients SET DateOfBirth = '1950-01-01'")
    study = STUDY.model_copy(update={"criteria": [PNEUMONIA.criteria[0], *STUDY.criteria[:1]]})
    study.criteria[1] = study.criteria[1].model_copy(update={"id": "C2"})
    run = run_screening(study, LLM(RuleBasedClient()), conn)
    p001 = next(p for p in run.patients if p.patient_id == "P001")
    c1 = next(a for a in p001.assessments if a.criterion_id == "C1")
    assert c1.stage1.verdict == "met" and c1.stage1.citations[0].table == "Patients"
    assert c1.stage2 is None and not c1.flags
    assert [a.criterion_id for a in p001.assessments] == ["C1", "C2"]
    assert p001.status == FinalStatus.likely_eligible


def test_parallel_workers_give_the_same_results_faster(tmp_path, monkeypatch):
    """workers > 1 screens patients in threads, each with its own read-only connection to the warehouse file."""
    import time

    import config

    warehouse = tmp_path / "warehouse.db"
    build_db(warehouse).close()
    monkeypatch.setattr(config, "DB_PATH", warehouse)

    class SlowClient(RuleBasedClient):
        def _create(self, **kwargs):
            time.sleep(0.01)  # stands in for model latency
            return super()._create(**kwargs)

    def timed(workers):
        start = time.perf_counter()
        run = run_screening(STUDY, LLM(SlowClient()), workers=workers)
        return run, time.perf_counter() - start

    one, t1 = timed(1)
    many, t4 = timed(4)
    assert [p.model_dump() for p in many.patients] == [p.model_dump() for p in one.patients]
    assert many.likely_eligible == one.likely_eligible and many.excluded == one.excluded
    assert t4 < t1 * 0.75


def test_given_connection_stays_sequential():
    steps = []
    run_screening(STUDY, LLM(RuleBasedClient()), build_db(), on_step=steps.append, workers=4)
    assert any("Patient 5 of 5" in s["detail"] for s in steps)


def test_patients_and_criteria_in_parallel(tmp_path, monkeypatch):
    import config

    warehouse = tmp_path / "warehouse.db"
    build_db(warehouse).close()
    monkeypatch.setattr(config, "DB_PATH", warehouse)
    one = run_screening(STUDY, LLM(RuleBasedClient()))
    many = run_screening(STUDY, LLM(RuleBasedClient()), workers=2, criteria_workers=4)
    assert [p.model_dump() for p in many.patients] == [p.model_dump() for p in one.patients]


def test_single_model_mode_skips_stage_2(monkeypatch):
    from src import llm as llm_mod
    monkeypatch.setattr(llm_mod, "STAGE_OVERRIDES", {"verifier": False})
    steps = []
    run = run_screening(STUDY, LLM(RuleBasedClient()), build_db(), on_step=steps.append)
    assert all(a.stage2 is None for r in run.patients for a in r.assessments)
    assert not any(f.type == "stage_disagreement" for r in run.patients for a in r.assessments for f in a.flags)
    by_id = {r.patient_id: r.status.value for r in run.patients}
    assert by_id["P001"] == "likely_eligible" and by_id["P002"] == "excluded"
    assert any(s["step"] == 3 and s["state"] == "done" and "Not used" in s["detail"] for s in steps)
