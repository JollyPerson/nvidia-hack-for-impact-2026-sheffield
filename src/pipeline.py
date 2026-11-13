"""The screening run, step by step. Owns the stage order only: each step's logic lives in its own module.

    prefilter (code, SQL)  ->  stage 1 screener + stage 2 verifier (screening.py)
    ->  validation (validation.py, flags only)  ->  decide (decide.py, the final call)

`run_screening` reports progress through `on_step(step_dict)` so the web app can stream it, and returns a
`ScreeningRun` (src/models.py) ready for `store.save_run`.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        self._lock = threading.Lock()  # patients are screened in parallel threads

    def _timed(self, fn, role, *args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(role, *args, **kwargs)
        finally:
            with self._lock:
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


def _most_recent_first(conn: sqlite3.Connection, candidates: list) -> list:
    """Candidates ordered by the arrival time of their matching episode, newest first (ties by patient id)."""
    ids = [c.encounter_id for c in candidates]
    marks = ",".join("?" * len(ids))
    arrived = dict(conn.execute(
        f"SELECT EncounterID, AdmissionDateTime FROM Inpatients WHERE EncounterID IN ({marks}) "
        f"UNION ALL SELECT EncounterID, CheckInDateTime FROM ECDS WHERE EncounterID IN ({marks})", ids + ids
    ).fetchall())
    return sorted(candidates, key=lambda c: (arrived.get(c.encounter_id) or "", c.patient_id), reverse=True)


def _screen_all(llm, conn, study, patient_ids, workers, audit, emit, n_notes) -> list[PatientResult]:
    """screen_patient for every candidate, `workers` at a time. Results keep the candidates' order.

    The model server takes several requests at once (OLLAMA_NUM_PARALLEL), so patients run in parallel
    threads, each with its own read-only warehouse connection (sqlite connections stay on one thread).
    """
    if workers == 1:
        out = []
        for n, pid in enumerate(patient_ids, 1):
            emit(_step(2, "running", f"Patient {n} of {len(patient_ids)}: checking {n_notes} notes criteria.",
                       model=config.SCREENER_MODEL))
            out.append(screen_patient(llm, conn, study, pid, audit=audit))
        return out

    local = threading.local()

    def one(pid: str) -> PatientResult:
        if not hasattr(local, "conn"):
            # Read-only, used by this thread only; check_same_thread=False just lets the main thread close it.
            local.conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, check_same_thread=False)
            local.conn.row_factory = sqlite3.Row
            opened.append(local.conn)
        return screen_patient(llm, local.conn, study, pid, audit=audit)

    opened: list[sqlite3.Connection] = []
    results: dict[str, PatientResult] = {}
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="screen") as pool:
            futures = {pool.submit(one, pid): pid for pid in patient_ids}
            for f in as_completed(futures):
                results[futures[f]] = f.result()
                done = len(results)
                emit(_step(2, "running", f"Screened {done} of {len(patient_ids)} patients ({workers} at a time).",
                           model=config.SCREENER_MODEL))
                shortlisted = sum(r.shortlisted for r in results.values())
                if shortlisted:
                    emit(_step(3, "running", f"Re-checked {shortlisted} shortlisted patients so far.",
                               model=config.VERIFIER_MODEL))
    finally:
        for c in opened:
            c.close()
    return [results[pid] for pid in patient_ids]


def run_screening(
    study: Study,
    llm: LLM | None = None,
    conn: sqlite3.Connection | None = None,
    on_step: OnStep | None = None,
    run_id: str = "",
    max_candidates: int | None = None,
    now: datetime | None = None,
    audit: list | None = None,
    workers: int = 1,
) -> ScreeningRun:
    """Screen one study end to end. Opens its own read-only warehouse connection unless one is given.

    workers > 1 screens that many patients at once (each thread opens its own connection), so it applies
    only when the pipeline opens the warehouse itself."""
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
            detail += f" Screening the {max_candidates} most recently admitted (demo limit)."
            candidates = _most_recent_first(conn, candidates)[:max_candidates]
        run.candidates_after_sql = len(candidates)
        emit(_step(1, "done", detail, time.perf_counter() - t))

        # 2 and 3. Stage 1 screener, then stage 2 verifier, for several patients at once
        timed = _TimedLLM(llm or LLM())
        n_notes = sum(c.kind == "notes" for c in study.criteria)
        workers = max(1, min(workers, len(candidates))) if own_conn else 1  # a given connection stays on this thread
        emit(_step(2, "running", f"Reading notes for {len(candidates)} patients, {workers} at a time.",
                   model=config.SCREENER_MODEL))
        t = time.perf_counter()
        results = _screen_all(timed, conn, study, [c.patient_id for c in candidates], workers, audit, emit, n_notes)
        wall = time.perf_counter() - t
        run.shortlisted_stage1 = sum(r.shortlisted for r in results)
        errors = sum(v is not None and v.reasoning.startswith(("model server error", "screening error"))
                     for r in results for a in r.assessments for v in (a.stage1, a.stage2))
        # With patients in parallel the stages overlap, so split the wall time by each model's share of the work.
        total = (timed.seconds["screener"] + timed.seconds["verifier"]) or 1.0
        emit(_step(2, "done", f"Checked {n_notes} notes criteria for {len(results)} candidates, one call per "
                              f"criterion, {workers} patients at a time. Shortlisted {run.shortlisted_stage1}."
                              + (f" {errors} model calls failed and count as insufficient." if errors else ""),
                   wall * timed.seconds["screener"] / total, config.SCREENER_MODEL))
        emit(_step(3, "done", f"Independently re-read the records of the {run.shortlisted_stage1} shortlisted "
                              f"patients. Stages 1 and 2 took {wall:.0f}s together.",
                   wall * timed.seconds["verifier"] / total, config.VERIFIER_MODEL))

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
