"""The screening run, step by step. Owns the stage order only: each step's logic lives in its own module.

    prefilter (code, SQL)  ->  stage 1 screener + stage 2 verifier (screening.py)
    ->  validation (validation.py, flags only)  ->  decide (decide.py, the final call)

`run_screening` reports progress through `on_step(step_dict)` so the web app can stream it, and returns a
`ScreeningRun` (src/models.py) ready for `store.save_run`.
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Callable

import config
from src.decide import decide
from src.llm import LLM
from src.models import CriterionAssessment, FinalStatus, PatientResult, ScreeningRun, Study, Verdict, VerdictValue
from src.prefilter import connect_readonly, run_prefilter
from src.screening import screen_patient
from src.validation import validate_patient

STEPS = [
    "Prefilter: SQL on the warehouse",
    "Stage 1: screener reads notes",
    "Stage 2: verifier re-checks original records",
    "Validation: check every citation",
    "Decide: code combines verdicts and flags",
]

OnStep = Callable[[dict], None]


class _TimedLLM:
    """Wraps the LLM gateway to time each role, so the progress list can show stage 1 and stage 2 separately."""

    def __init__(self, llm: LLM):
        self.llm = llm
        self.seconds: dict[str, float] = defaultdict(float)
        self.calls: Counter = Counter()

    def _timed(self, fn, role, *args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(role, *args, **kwargs)
        finally:
            self.seconds[role] += time.perf_counter() - start
            self.calls[role] += 1

    def chat(self, role, *args, **kwargs):
        return self._timed(self.llm.chat, role, *args, **kwargs)

    def verdict(self, role, *args, **kwargs):
        return self._timed(self.llm.verdict, role, *args, **kwargs)


def _step(n: int, state: str, detail: str = "", seconds: float | None = None, model: str | None = None) -> dict:
    d = {"step": n, "name": STEPS[n - 1], "state": state, "detail": detail}
    if seconds is not None:
        d["seconds"] = round(seconds, 2)
    if model:
        d["model"] = model
    return d


def _code_assessments(study: Study, citations: dict) -> list[CriterionAssessment]:
    """Structured inclusions the prefilter checked, as `met` verdicts citing the warehouse rows."""
    return [
        CriterionAssessment(
            criterion_id=cid,
            stage1=Verdict(verdict=VerdictValue.met, reasoning="Checked by code against the warehouse (SQL prefilter).",
                           citations=cites),
        )
        for cid, cites in citations.items()
        if cites
    ]


def run_screening(
    study: Study,
    llm: LLM | None = None,
    conn: sqlite3.Connection | None = None,
    on_step: OnStep | None = None,
    run_id: str = "",
    max_candidates: int | None = None,
    now: datetime | None = None,
    audit: list | None = None,
) -> ScreeningRun:
    """Screen one study end to end. Opens its own read-only warehouse connection unless one is given."""
    emit = on_step or (lambda _d: None)
    own_conn = conn is None
    conn = conn or connect_readonly(config.DB_PATH)
    conn.row_factory = sqlite3.Row  # validation.py and tools.py read columns by name
    run = ScreeningRun(run_id=run_id or "run", study_id=study.id or "", status="running", started_at=datetime.now())
    try:
        # 1. Prefilter
        emit(_step(1, "running", "Building a parameterised query from the structured criteria."))
        t = time.perf_counter()
        pre = run_prefilter(study, conn, now=now)
        candidates = pre.candidates
        detail = (f"{len(candidates)} of {pre.patients_considered} patients in the warehouse passed "
                  f"{sum(c.kind == 'structured' for c in study.criteria)} structured criteria.")
        if max_candidates is not None and len(candidates) > max_candidates:
            detail += f" Screening the first {max_candidates} (demo limit)."
            candidates = candidates[:max_candidates]
        run.candidates_after_sql = len(candidates)
        emit(_step(1, "done", detail, time.perf_counter() - t))

        # 2 and 3. Stage 1 screener, then stage 2 verifier for each shortlisted patient
        timed = _TimedLLM(llm or LLM())
        n_notes = sum(c.kind == "notes" for c in study.criteria)
        results: list[PatientResult] = []
        for n, cand in enumerate(candidates, 1):
            emit(_step(2, "running", f"Patient {n} of {len(candidates)}: checking {n_notes} notes criteria.",
                       model=config.SCREENER_MODEL))
            r = screen_patient(timed, conn, study, cand.patient_id, audit=audit)
            results.append(r)
            shortlisted = sum(x.shortlisted for x in results)
            if shortlisted:
                emit(_step(3, "running", f"Re-checked {shortlisted} shortlisted patients so far.",
                           model=config.VERIFIER_MODEL))
        run.shortlisted_stage1 = sum(r.shortlisted for r in results)
        errors = sum(v is not None and v.reasoning.startswith(("model server error", "screening error"))
                     for r in results for a in r.assessments for v in (a.stage1, a.stage2))
        emit(_step(2, "done", f"Checked {n_notes} notes criteria for {len(results)} candidates, one call per "
                              f"criterion. Shortlisted {run.shortlisted_stage1}."
                              + (f" {errors} model calls failed and count as insufficient." if errors else ""),
                   timed.seconds["screener"], config.SCREENER_MODEL))
        emit(_step(3, "done", f"Independently re-read the records of the {run.shortlisted_stage1} shortlisted "
                              "patients.", timed.seconds["verifier"], config.VERIFIER_MODEL))

        # 4. Validation: model citations only (code-made citations come from the warehouse itself)
        emit(_step(4, "running", "Checking every quote against the cited record."))
        t = time.perf_counter()
        for r in results:
            validate_patient(conn, r)
        failed = sum(f.type == "citation_failed" for r in results for a in r.assessments for f in a.flags)
        disagree = sum(f.type == "stage_disagreement" for r in results for a in r.assessments for f in a.flags)
        emit(_step(4, "done", f"{failed} quotes could not be found, {disagree} disagreements flagged. "
                              "Nothing was removed.", time.perf_counter() - t))

        # 5. Decide
        emit(_step(5, "running"))
        t = time.perf_counter()
        citations = {c.patient_id: c.citations for c in candidates}
        order = {cid: i for i, cid in enumerate(c.id or f"C{i + 1}" for i, c in enumerate(study.criteria))}
        for r in results:
            r.assessments = sorted(r.assessments + _code_assessments(study, citations[r.patient_id]),
                                   key=lambda a: order.get(a.criterion_id, 99))
            decide(study, r)
        counts = Counter(r.status for r in results)
        run.confirmed_stage2 = sum(r.shortlisted and r.status != FinalStatus.excluded for r in results)
        run.likely_eligible = counts[FinalStatus.likely_eligible]
        run.needs_review = counts[FinalStatus.needs_review]
        run.excluded = counts[FinalStatus.excluded]
        run.patients = results
        emit(_step(5, "done", f"{run.likely_eligible} likely eligible, {run.needs_review} need review, "
                              f"{run.excluded} excluded.", time.perf_counter() - t))
        run.status = "complete"
    except Exception:
        run.status = "failed"
        raise
    finally:
        run.finished_at = datetime.now()
        if own_conn:
            conn.close()
    return run
