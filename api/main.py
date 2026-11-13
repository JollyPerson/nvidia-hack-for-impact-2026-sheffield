"""All HTTP endpoints. Serves the JSON API under /api and the plain HTML pages from web/.

Screening runs the real pipeline (src/pipeline.py) in a background thread and streams its progress to the
browser over server-sent events. Studies, runs, decisions and the audit log live in the app database
(src/store.py, data/app.db); the demo studies are seeded on start-up (data/seed_studies.py).

Set ADVOLARE_MOCK=true to use the canned results in api/mock_data.py instead (no warehouse or model
server needed). See api/README.md.
"""
import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from api import extras, features, mock_data
from api.schemas import (Contact, DecisionRequest, DecisionResponse, DecisionValue, DraftRequest, DraftResponse,
                         Funnel, PatientView, RunView, StudySummary, TraceStep)
from data import seed_studies
from src import llm as llm_mod
from src import pipeline, store
from src.models import AuditEntry, CriterionKind, Decision, PatientResult, ScreeningRun, Study
from src.prefilter import PrefilterError, parse_value

log = logging.getLogger("advolare.api")

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _env_true(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def mock_mode() -> bool:
    """ADVOLARE_MOCK=true: canned results from api/mock_data.py instead of the pipeline."""
    return _env_true("ADVOLARE_MOCK")


LIMIT_OVERRIDE: dict[str, int] = {}  # developer mode: {"patients": n} for the next runs (0 = no limit)


def max_candidates() -> int | None:
    """ADVOLARE_MAX_CANDIDATES: screen at most this many prefilter candidates (each costs several model calls).
    0 means no limit; empty means 5. A developer-mode override wins until reset."""
    if "patients" in LIMIT_OVERRIDE:
        return LIMIT_OVERRIDE["patients"] or None
    raw = os.environ.get("ADVOLARE_MAX_CANDIDATES", "").strip()  # compose passes it empty when unset
    n = int(raw) if raw else 5
    return n or None


def workers() -> int:
    """ADVOLARE_WORKERS: patients screened at once."""
    return max(1, int(os.environ.get("ADVOLARE_WORKERS", "").strip() or 2))


def criteria_workers() -> int:
    """ADVOLARE_CRITERIA_WORKERS: criteria checked at once per patient. Keep workers x this within the
    model server's OLLAMA_NUM_PARALLEL (8 on the Spark)."""
    return max(1, int(os.environ.get("ADVOLARE_CRITERIA_WORKERS", "").strip() or 4))


# Seconds between mock pipeline steps when streaming progress (0 in tests).
MOCK_STEP_DELAY = float(os.environ.get("ADVOLARE_MOCK_STEP_DELAY", "0.8"))


def make_llm():
    """The model gateway for a real run. None lets the pipeline build src.llm.LLM(); tests swap in a fake."""
    return None


app = FastAPI(title="Advolare", description="Clinical Trial Screening System (synthetic data only)")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
app.mount("/assets", StaticFiles(directory=WEB / "static" / "nhsuk" / "assets"), name="nhsuk-assets")


# ---------- App database and live runs ----------
@dataclass
class LiveRun:
    """A run this process started: its progress events (for SSE) and the finished run once it is done."""
    run_id: str
    study_id: str
    events: list[dict] = field(default_factory=list)
    trace: dict[int, TraceStep] = field(default_factory=dict)
    done: bool = False
    error: str = ""
    removed: list[dict] = field(default_factory=list)  # mock do-not-contact groups
    funnel_extra: dict = field(default_factory=dict)  # mock-only funnel numbers


LIVE: dict[str, LiveRun] = {}
MOCK_VIEWS: dict[str, PatientView] = {}  # age, sex and summary for mock patients (not in the warehouse)


def init_app(reset: bool = False) -> None:
    """Create the app database if needed and add the demo studies. reset=True starts from empty (tests)."""
    seed_studies.seed(Path(store.APP_DB_PATH), reset=reset)
    if reset:
        LIVE.clear()


init_app()


def _log(study_id: str, action: str, who: str = "Clinician", patient_id: str = "", note: str = ""):
    store.append_audit(AuditEntry(when=datetime.now(), study_id=study_id, who=who, action=action,
                                  patient_id=patient_id, note=note))


def _study(study_id: str) -> Study:
    study = store.get_study(study_id)
    if study is None:
        raise HTTPException(404, "Study not found")
    return study


# ---------- Warehouse lookups (code only; nothing here goes to a model) ----------
def _warehouse() -> sqlite3.Connection | None:
    path = Path(config.DB_PATH)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _age(dob: str) -> int | None:
    try:
        born = date.fromisoformat(dob[:10])
    except (TypeError, ValueError):
        return None
    today = config.REFERENCE_NOW.date()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _demographics(patient_ids: list[str]) -> dict[str, tuple[int | None, str]]:
    out = {pid: (MOCK_VIEWS[pid].age, MOCK_VIEWS[pid].sex) for pid in patient_ids if pid in MOCK_VIEWS}
    wanted = [pid for pid in patient_ids if pid not in out]
    conn = _warehouse() if wanted else None
    if conn is not None:
        with conn:
            marks = ",".join("?" * len(wanted))
            for r in conn.execute(f"SELECT LocalPatientIdentifier, DateOfBirth, Sex FROM Patients "
                                  f"WHERE LocalPatientIdentifier IN ({marks})", wanted):
                out[r["LocalPatientIdentifier"]] = (_age(r["DateOfBirth"]), r["Sex"])
        conn.close()
    return out


def _contact_from_warehouse(patient_id: str) -> Contact | None:
    """PatientContacts is read only here, after approval. It never goes to a model or a tool."""
    conn = _warehouse()
    if conn is None:
        return None
    try:
        r = conn.execute("SELECT * FROM PatientContacts WHERE LocalPatientIdentifier = ?", (patient_id,)).fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    return Contact(patient_id=patient_id, mobile_number=r["MobileNumber"], landline=r["Landline"], email=r["Email"],
                   guardian_name=r["GuardianName"], guardian_phone=r["GuardianPhone"])


# ---------- Building what the pages show ----------
def _summary(r: PatientResult) -> str:
    """One line for the results table, from the decide step's reasons (screening's own 'Stage 1:' notes left out)."""
    if r.patient_id in MOCK_VIEWS and MOCK_VIEWS[r.patient_id].summary:
        return MOCK_VIEWS[r.patient_id].summary
    reasons = [x for x in r.reasons if not x.startswith(("Stage 1:", "Not mentioned in the notes"))] or \
        [x for x in r.reasons if not x.startswith("Stage 1:")] or r.reasons
    return "; ".join(reasons[:3]) + (f"; and {len(reasons) - 3} more" if len(reasons) > 3 else "")


def _view(run: ScreeningRun) -> RunView:
    live = LIVE.get(run.run_id)
    decisions = store.get_decisions(run.run_id)
    people = _demographics([r.patient_id for r in run.patients])
    patients = []
    for r in run.patients:
        age, sex = people.get(r.patient_id, (None, ""))
        d = decisions.get(r.patient_id)
        patients.append(PatientView(patient_id=r.patient_id, age=age, sex=sex, summary=_summary(r),
                                    decision=DecisionValue(d.decision) if d else DecisionValue.pending, result=r))
    funnel = None
    if run.status == "complete":
        extra = live.funnel_extra if live else {}
        funnel = Funnel(removed_before_screening=live.removed if live else [],
                        candidates_after_sql=run.candidates_after_sql + extra.get("candidates_after_sql", 0),
                        shortlisted_stage1=run.shortlisted_stage1, confirmed_stage2=run.confirmed_stage2,
                        likely_eligible=run.likely_eligible, needs_review=run.needs_review,
                        excluded=run.excluded + extra.get("excluded", 0))
    trace = [live.trace[k] for k in sorted(live.trace)] if live else []
    return RunView(run_id=run.run_id, study_id=run.study_id, status=run.status, started_at=run.started_at,
                   finished_at=run.finished_at, error=live.error if live else "", trace=trace, funnel=funnel,
                   patients=patients)


def _latest(study_id: str) -> RunView:
    run = store.get_latest_run(study_id)
    if run is None:
        raise HTTPException(404, "This study has not been screened yet")
    return _view(run)


def _patient(study_id: str, patient_id: str) -> PatientView:
    for p in _latest(study_id).patients:
        if p.patient_id == patient_id:
            return p
    raise HTTPException(404, "Patient not in this screening run")


# ---------- Running a screening ----------
def _mock_pipeline(study: Study, live: LiveRun, emit) -> ScreeningRun:
    """Canned results with the pipeline's shapes (ADVOLARE_MOCK=true)."""
    views = mock_data.patients_for(study)
    for v in views:
        MOCK_VIEWS[v.patient_id] = v
    funnel = mock_data.funnel_for(views)
    live.removed = [g.model_dump() for g in funnel.removed_before_screening]
    n_hidden = funnel.candidates_after_sql - len(views)  # candidates the mock drops at stage 1 without detail
    live.funnel_extra = {"candidates_after_sql": n_hidden, "excluded": n_hidden}
    for t in mock_data.trace_for(study, funnel):
        model = {"SCREENER": config.SCREENER_MODEL, "VERIFIER": config.VERIFIER_MODEL}.get(t.model or "", t.model)
        emit({"step": t.step, "name": t.name, "state": "running", "detail": ""})
        time.sleep(MOCK_STEP_DELAY)
        emit({**t.model_dump(), "model": model, "state": "done"})
    statuses = [v.result.status.value for v in views]
    return ScreeningRun(run_id=live.run_id, study_id=study.id, status="complete", started_at=datetime.now(),
                        finished_at=datetime.now(), candidates_after_sql=len(views),
                        shortlisted_stage1=funnel.shortlisted_stage1, confirmed_stage2=funnel.confirmed_stage2,
                        likely_eligible=statuses.count("likely_eligible"), needs_review=statuses.count("needs_review"),
                        excluded=statuses.count("excluded"), patients=[v.result for v in views])


def _worker(study: Study, live: LiveRun, started: ScreeningRun) -> None:
    def emit(d: dict):
        live.events.append(d)
        if d.get("state") == "done":
            live.trace[d["step"]] = TraceStep(step=d["step"], name=d["name"], detail=d.get("detail", ""),
                                              seconds=d.get("seconds", 0.0), model=d.get("model"))

    t0 = time.perf_counter()
    try:
        if mock_mode():
            run = _mock_pipeline(study, live, emit)
        else:
            run = pipeline.run_screening(study, llm=make_llm(), on_step=emit, run_id=live.run_id,
                                         max_candidates=max_candidates(), workers=workers(),
                                         criteria_workers=criteria_workers())
        run.started_at = started.started_at
        store.save_run(run)
        _log(study.id, f"Screening finished in {time.perf_counter() - t0:.0f}s: {run.likely_eligible} likely "
                       f"eligible, {run.needs_review} need review, {run.excluded} excluded", who="Advolare pipeline")
    except Exception as e:  # noqa: BLE001 - report any failure to the page rather than hanging it
        log.exception("screening run %s failed", live.run_id)
        live.error = f"{type(e).__name__}: {e}"
        store.save_run(started.model_copy(update={"status": "failed", "finished_at": datetime.now()}))
        _log(study.id, "Screening failed", who="Advolare pipeline", note=live.error)
    finally:
        live.done = True


# ---------- Studies ----------
@app.get("/api/studies", response_model=list[StudySummary])
def list_studies():
    return [StudySummary(id=s.id, title=s.title, short_title=s.short_title, condition=s.condition,
                         status=s.status.value, target_sample_size=s.target_sample_size,
                         screened=store.get_latest_run(s.id) is not None) for s in store.list_studies()]


@app.post("/api/studies", response_model=Study, status_code=201)
def create_study(body: Study):
    """Save a study. Code assigns the study and criterion IDs; any IDs sent by the browser are ignored."""
    if not body.criteria:
        raise HTTPException(422, "Add at least one eligibility criterion")
    for i, c in enumerate(body.criteria):
        if c.kind == CriterionKind.structured:
            try:
                parse_value(c, f"C{i + 1}")  # the prefilter's own field and operator whitelist
            except PrefilterError as e:
                raise HTTPException(422, str(e))
    study = store.create_study(body.model_copy(update={"id": None, "criteria": [
        c.model_copy(update={"id": None}) for c in body.criteria]}))
    _log(study.id, f"Created study with {len(study.criteria)} criteria")
    return study


@app.get("/api/studies/{study_id}", response_model=Study)
def get_study(study_id: str):
    return _study(study_id)


@app.post("/api/criteria/draft", response_model=DraftResponse, dependencies=[Depends(features.require("draft_criteria"))])
def draft_criteria(body: DraftRequest):
    """Turn a plain-English description into draft criteria rows for the clinician to check."""
    criteria, assumptions = mock_data.draft_criteria(body.text)
    return DraftResponse(criteria=criteria, assumptions=assumptions)


@app.get("/api/criteria/example", dependencies=[Depends(features.require("draft_criteria"))])
def example_description():
    return {"text": mock_data.EXAMPLE_DESCRIPTION}


# ---------- Screening ----------
@app.post("/api/studies/{study_id}/runs", response_model=RunView, status_code=202)
def start_run(study_id: str):
    study = _study(study_id)
    if any(not r.done and r.study_id == study_id for r in LIVE.values()):
        raise HTTPException(409, "This study is already being screened")
    run_id = uuid.uuid4().hex[:8]
    started = store.save_run(ScreeningRun(run_id=run_id, study_id=study_id, status="running",
                                          started_at=datetime.now()))
    live = LIVE[run_id] = LiveRun(run_id=run_id, study_id=study_id)
    _log(study_id, "Started screening" + (" (mock results)" if mock_mode() else ""))
    threading.Thread(target=_worker, args=(study, live, started), daemon=True, name=f"run-{run_id}").start()
    return RunView(run_id=run_id, study_id=study_id, status="running", started_at=started.started_at)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """Server-sent events: `step` events as the pipeline moves (running, then done, per step), then `complete`
    (or `failed` with the error)."""
    live = LIVE.get(run_id)
    if live is None:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")

    async def stream():
        if live is None:  # started by an earlier process: nothing to replay
            yield f"event: complete\ndata: {json.dumps({'run_id': run_id})}\n\n"
            return
        sent = 0
        while True:
            while sent < len(live.events):
                yield f"event: step\ndata: {json.dumps(live.events[sent])}\n\n"
                sent += 1
            if live.done and sent >= len(live.events):
                break
            await asyncio.sleep(0.2)
        if live.error:
            yield f"event: failed\ndata: {json.dumps({'run_id': run_id, 'error': live.error})}\n\n"
        else:
            yield f"event: complete\ndata: {json.dumps({'run_id': run_id})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/studies/{study_id}/results", response_model=RunView)
def get_results(study_id: str):
    _study(study_id)
    return _latest(study_id)


@app.get("/api/studies/{study_id}/patients/{patient_id}", response_model=PatientView)
def get_patient(study_id: str, patient_id: str):
    return _patient(study_id, patient_id)


@app.post("/api/studies/{study_id}/patients/{patient_id}/decision", response_model=DecisionResponse)
def decide(study_id: str, patient_id: str, body: DecisionRequest):
    run = _latest(study_id)
    _patient(study_id, patient_id)
    store.record_decision(Decision(patient_id=patient_id, run_id=run.run_id, decision=body.decision.value,
                                   note=body.note, who="Clinician", when=datetime.now()))
    _log(study_id, {"approved": "Approved for contact", "rejected": "Rejected", "pending": "Decision cleared"}[body.decision.value],
         patient_id=patient_id, note=body.note)
    patient = _patient(study_id, patient_id)
    return DecisionResponse(patient=patient, contact=_contact_if_approved(patient))


def _contact_if_approved(patient: PatientView) -> Contact | None:
    """PatientContacts is joined only after the clinician approves. It never goes to a model."""
    if patient.decision != DecisionValue.approved:
        return None
    if patient.patient_id in mock_data.CONTACTS:
        return mock_data.CONTACTS[patient.patient_id]
    return _contact_from_warehouse(patient.patient_id) or Contact(patient_id=patient.patient_id)


@app.get("/api/studies/{study_id}/patients/{patient_id}/contact", response_model=Contact)
def get_contact(study_id: str, patient_id: str):
    contact = _contact_if_approved(_patient(study_id, patient_id))
    if contact is None:
        raise HTTPException(403, "Contact details are shown only after you approve this patient")
    return contact


@app.get("/api/studies/{study_id}/audit", response_model=list[AuditEntry])
def get_audit(study_id: str):
    _study(study_id)
    return list(reversed(store.read_audit(study_id)))  # newest first


# ---------- Extras ----------
@app.get("/api/studies/{study_id}/fhir", dependencies=[Depends(features.require("fhir_export"))])
def export_fhir(study_id: str):
    """HL7 FHIR R5 Bundle (ResearchStudy + Group) with SNOMED CT codes, for registries and other systems."""
    study = _study(study_id)
    return JSONResponse(extras.to_fhir(study), headers={
        "Content-Disposition": f'attachment; filename="{study.short_title or study.id}-fhir.json"'})


@app.get("/api/studies/{study_id}/snomed", dependencies=[Depends(features.require("fhir_export"))])
def criteria_snomed(study_id: str):
    return {c.id: dict(zip(("code", "display"), extras.snomed_for(c))) for c in _study(study_id).criteria}


@app.get("/api/studies/{study_id}/what-if", dependencies=[Depends(features.require("what_if"))])
def what_if(study_id: str):
    """How the shortlist changes without each criterion. Reuses the run's verdicts: no new model calls."""
    return extras.what_if(_study(study_id), _latest(study_id).patients)


@app.get("/api/studies/{study_id}/fairness", dependencies=[Depends(features.require("fairness"))])
def fairness(study_id: str):
    _study(study_id)
    return extras.fairness(_latest(study_id).patients)


@app.get("/api/studies/{study_id}/patients/{patient_id}/letter", dependencies=[Depends(features.require("invitation_letter"))])
def invitation_letter(study_id: str, patient_id: str):
    """A draft for the research team to check and send themselves. Only after approval; never sent by Advolare."""
    contact = _contact_if_approved(_patient(study_id, patient_id))
    if contact is None:
        raise HTTPException(403, "The invitation letter is available only after you approve this patient")
    return {"text": extras.letter(_study(study_id), contact)}


def _log_rows(study_id: str):
    run = _latest(study_id)
    return extras.screening_log(run.patients, run.funnel.candidates_after_sql - run.funnel.shortlisted_stage1)


@app.get("/api/studies/{study_id}/screening-log", dependencies=[Depends(features.require("screening_log"))])
def screening_log(study_id: str):
    _study(study_id)
    return _log_rows(study_id)


@app.get("/api/studies/{study_id}/screening-log.csv", dependencies=[Depends(features.require("screening_log"))])
def screening_log_csv(study_id: str):
    study = _study(study_id)
    return PlainTextResponse(extras.screening_log_csv(_log_rows(study_id)), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{study.short_title or study.id}-screening-log.csv"'})


@app.get("/api/features")
def get_features():
    """Which extras are switched on. The pages read this to show or hide tabs and buttons."""
    return features.flags()


class FeatureToggle(BaseModel):
    name: str
    enabled: bool | None  # None = back to the env setting


@app.get("/api/dev/features", dependencies=[Depends(features.require_dev_mode)])
def dev_features():
    """Developer mode: every feature with its state and where the state comes from."""
    return features.describe()


@app.post("/api/dev/features", dependencies=[Depends(features.require_dev_mode)])
def dev_toggle(body: FeatureToggle):
    if body.name not in features.FEATURES:
        raise HTTPException(422, f"Unknown feature: {body.name}")
    features.set_override(body.name, body.enabled)
    _log("", f"Developer mode: {body.name} " + ("reset to deploy setting" if body.enabled is None else
                                                     "on" if body.enabled else "off"), who="Developer")
    return features.describe()


@app.delete("/api/dev/features", dependencies=[Depends(features.require_dev_mode)])
def dev_reset():
    features.clear_overrides()
    llm_mod.REASONING_OVERRIDES.clear()
    LIMIT_OVERRIDE.clear()
    llm_mod.STAGE_OVERRIDES.clear()
    return features.describe()


class VerifierToggle(BaseModel):
    enabled: bool | None  # None = back to ADVOLARE_VERIFIER


def _verifier():
    return {"enabled": llm_mod.verifier_on(), "model": config.VERIFIER_MODEL,
            "source": "developer mode" if "verifier" in llm_mod.STAGE_OVERRIDES else "ADVOLARE_VERIFIER (default on)"}


@app.get("/api/dev/verifier", dependencies=[Depends(features.require_dev_mode)])
def dev_verifier():
    """Developer mode: whether stage 2 (the verifier model) runs. Off = single-model mode."""
    return _verifier()


@app.post("/api/dev/verifier", dependencies=[Depends(features.require_dev_mode)])
def dev_set_verifier(body: VerifierToggle):
    if body.enabled is None:
        llm_mod.STAGE_OVERRIDES.pop("verifier", None)
    else:
        llm_mod.STAGE_OVERRIDES["verifier"] = body.enabled
    _log("", f"Developer mode: stage 2 verifier {'on' if llm_mod.verifier_on() else 'off (single model)'}", who="Developer")
    return _verifier()


class LimitSetting(BaseModel):
    patients: int | None = Field(default=None, ge=0, le=500)  # None = back to ADVOLARE_MAX_CANDIDATES; 0 = no limit


def _limit():
    n = max_candidates()
    return {"patients": n or 0, "source": "developer mode" if "patients" in LIMIT_OVERRIDE else
            "ADVOLARE_MAX_CANDIDATES (default 5)"}


@app.get("/api/dev/limit", dependencies=[Depends(features.require_dev_mode)])
def dev_limit():
    """Developer mode: how many prefilter candidates the next run screens with the models."""
    return _limit()


@app.post("/api/dev/limit", dependencies=[Depends(features.require_dev_mode)])
def dev_set_limit(body: LimitSetting):
    if body.patients is None:
        LIMIT_OVERRIDE.clear()
    else:
        LIMIT_OVERRIDE["patients"] = body.patients
    _log("", f"Developer mode: patients per run set to {_limit()['patients'] or 'no limit'}", who="Developer")
    return _limit()


class ReasoningToggle(BaseModel):
    role: str  # "screener" or "verifier"
    reasoning: bool | None  # None = back to the env setting


def _models():
    return [{"role": role, "model": config.MODELS[role], "reasoning": llm_mod.reasoning_on(role),
             "max_output_tokens": llm_mod.max_output_tokens(role),
             "source": "developer toggle" if role in llm_mod.REASONING_OVERRIDES else
             f"{role.upper()}_REASONING / LLM_REASONING (default off)"} for role in ("screener", "verifier")]


@app.get("/api/dev/models", dependencies=[Depends(features.require_dev_mode)])
def dev_models():
    """Developer mode: each model's reasoning (thinking) setting and where it comes from."""
    return _models()


@app.post("/api/dev/models", dependencies=[Depends(features.require_dev_mode)])
def dev_toggle_reasoning(body: ReasoningToggle):
    if body.role not in config.MODELS:
        raise HTTPException(422, f"Unknown role: {body.role}")
    if body.reasoning is None:
        llm_mod.REASONING_OVERRIDES.pop(body.role, None)
    else:
        llm_mod.REASONING_OVERRIDES[body.role] = body.reasoning
    _log("", f"Developer mode: {body.role} reasoning " + ("reset to deploy setting" if body.reasoning is None else
                                                         "on" if body.reasoning else "off"), who="Developer")
    return _models()


@app.get("/api/health")
def health():
    return {"ok": True, "screener": config.SCREENER_MODEL, "verifier": config.VERIFIER_MODEL,
            "reasoning": {m["role"]: m["reasoning"] for m in _models()}, "verifier_on": llm_mod.verifier_on(),
            "max_candidates": max_candidates() or 0}


# ---------- Pages ----------
@app.get("/", include_in_schema=False)
def page_index():
    return FileResponse(WEB / "index.html")


@app.get("/studies/new", include_in_schema=False)
def page_new_study():
    return FileResponse(WEB / "new_study.html")


@app.get("/studies/{study_id}", include_in_schema=False)
def page_study(study_id: str):
    return FileResponse(WEB / "study.html")
