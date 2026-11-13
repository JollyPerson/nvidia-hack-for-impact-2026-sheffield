"""All HTTP endpoints. Serves the JSON API under /api and the plain HTML pages from web/.

Until the real pipeline is wired in, screening results come from api/mock_data.py with the same
shapes as src/models.py
(wrapped in the API-only views in api/schemas.py). Swap `_run_pipeline` for src.pipeline without changing the pages.
"""
import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from api import extras, features, mock_data
from api.schemas import (AuditEntry, Contact, Decision, DecisionRequest, DecisionResponse, DraftRequest, DraftResponse,
                         PatientView, ScreeningRun, StudySummary)
from src.models import Study

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
# Seconds between mock pipeline steps when streaming progress (0 in tests). Goes when src.pipeline is wired in.
MOCK_STEP_DELAY = float(os.environ.get("ADVOLARE_MOCK_STEP_DELAY", "0.8"))

app = FastAPI(title="Advolare", description="Clinical Trial Screening System (synthetic data only)")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
app.mount("/assets", StaticFiles(directory=WEB / "static" / "nhsuk" / "assets"), name="nhsuk-assets")


# ---------- In-memory store (fine for the demo; one process) ----------
class Store:
    def __init__(self):
        self.reset()

    def reset(self):
        self.studies: dict[str, Study] = {s.id: s.model_copy(deep=True) for s in mock_data.STUDIES}
        self.runs: dict[str, ScreeningRun] = {}
        self.latest_run: dict[str, str] = {}
        self.audit: list[dict] = []

    def log(self, study_id: str, action: str, who: str = "Clinician", patient_id: str = "", note: str = ""):
        self.audit.insert(0, {"when": datetime.now().isoformat(timespec="seconds"), "study_id": study_id,
                              "who": who, "action": action, "patient_id": patient_id, "note": note})


store = Store()


def _study(study_id: str) -> Study:
    if study_id not in store.studies:
        raise HTTPException(404, "Study not found")
    return store.studies[study_id]


def _latest(study_id: str) -> ScreeningRun:
    run_id = store.latest_run.get(study_id)
    if not run_id:
        raise HTTPException(404, "This study has not been screened yet")
    return store.runs[run_id]


def _patient(study_id: str, patient_id: str) -> PatientView:
    for r in _latest(study_id).patients:
        if r.patient_id == patient_id:
            return r
    raise HTTPException(404, "Patient not in this screening run")


def _run_pipeline(study: Study) -> ScreeningRun:
    """Mock of src.pipeline: prefilter -> stage 1 -> stage 2 -> validation -> decide."""
    patients = mock_data.patients_for(study)
    funnel = mock_data.funnel_for(patients)
    trace = mock_data.trace_for(study, funnel)
    for t in trace:
        if t.model == "SCREENER":
            t.model = config.SCREENER_MODEL
        elif t.model == "VERIFIER":
            t.model = config.VERIFIER_MODEL
    return ScreeningRun(run_id=uuid.uuid4().hex[:8], study_id=study.id, status="running", trace=trace,
                        funnel=funnel, patients=patients)


# ---------- Studies ----------
@app.get("/api/studies", response_model=list[StudySummary])
def list_studies():
    return [StudySummary(id=s.id, title=s.title, short_title=s.short_title, condition=s.condition,
                         status=s.status.value, target_sample_size=s.target_sample_size,
                         screened=s.id in store.latest_run) for s in store.studies.values()]


@app.post("/api/studies", response_model=Study, status_code=201)
def create_study(body: Study):
    """Save a study. Code assigns the study and criterion IDs; any IDs sent by the browser are ignored."""
    if not body.criteria:
        raise HTTPException(422, "Add at least one eligibility criterion")
    study_id = f"S-{len(store.studies) + 1:03d}"
    criteria = [c.model_copy(update={"id": f"C{i}"}) for i, c in enumerate(body.criteria, 1)]
    study = body.model_copy(update={"id": study_id, "criteria": criteria})
    store.studies[study_id] = study
    store.log(study_id, f"Created study with {len(criteria)} criteria")
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
@app.post("/api/studies/{study_id}/runs", response_model=ScreeningRun, status_code=202)
def start_run(study_id: str):
    study = _study(study_id)
    run = _run_pipeline(study)
    store.runs[run.run_id] = run
    store.latest_run[study_id] = run.run_id
    store.log(study_id, "Started screening", who="Clinician")
    return run.model_copy(update={"trace": [], "patients": [], "funnel": None})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """Server-sent events: one `step` event per pipeline step, then `complete`."""
    if run_id not in store.runs:
        raise HTTPException(404, "Run not found")
    run = store.runs[run_id]

    async def stream():
        for step in run.trace:
            yield f"event: step\ndata: {json.dumps({'step': step.step, 'state': 'running', 'name': step.name})}\n\n"
            await asyncio.sleep(MOCK_STEP_DELAY)
            yield f"event: step\ndata: {step.model_dump_json()}\n\n"
        run.status = "complete"
        store.log(run.study_id, f"Screening finished: {run.funnel.likely_eligible} likely eligible, "
                                f"{run.funnel.needs_review} need review", who="Advolare pipeline")
        yield f"event: complete\ndata: {json.dumps({'run_id': run.run_id})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/studies/{study_id}/results", response_model=ScreeningRun)
def get_results(study_id: str):
    _study(study_id)
    return _latest(study_id)


@app.get("/api/studies/{study_id}/patients/{patient_id}", response_model=PatientView)
def get_patient(study_id: str, patient_id: str):
    return _patient(study_id, patient_id)


@app.post("/api/studies/{study_id}/patients/{patient_id}/decision", response_model=DecisionResponse)
def decide(study_id: str, patient_id: str, body: DecisionRequest):
    patient = _patient(study_id, patient_id)
    patient.decision = body.decision
    patient.result.approved = body.decision == Decision.approved
    store.log(study_id, {"approved": "Approved for contact", "rejected": "Rejected", "pending": "Decision cleared"}[body.decision.value],
              patient_id=patient_id, note=body.note)
    return DecisionResponse(patient=patient, contact=_contact_if_approved(patient))


def _contact_if_approved(patient: PatientView) -> Contact | None:
    """PatientContacts is joined only after the clinician approves. It never goes to a model."""
    if patient.decision != Decision.approved:
        return None
    return mock_data.CONTACTS.get(patient.patient_id) or Contact(
        patient_id=patient.patient_id, name="(synthetic)", mobile="07700 900000", email="patient@example.com")


@app.get("/api/studies/{study_id}/patients/{patient_id}/contact", response_model=Contact)
def get_contact(study_id: str, patient_id: str):
    contact = _contact_if_approved(_patient(study_id, patient_id))
    if contact is None:
        raise HTTPException(403, "Contact details are shown only after you approve this patient")
    return contact


@app.get("/api/studies/{study_id}/audit", response_model=list[AuditEntry])
def get_audit(study_id: str):
    _study(study_id)
    return [a for a in store.audit if a["study_id"] == study_id]


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
    store.log("", f"Developer mode: {body.name} " + ("reset to deploy setting" if body.enabled is None else
                                                     "on" if body.enabled else "off"), who="Developer")
    return features.describe()


@app.delete("/api/dev/features", dependencies=[Depends(features.require_dev_mode)])
def dev_reset():
    features.clear_overrides()
    return features.describe()


@app.get("/api/health")
def health():
    return {"ok": True, "screener": config.SCREENER_MODEL, "verifier": config.VERIFIER_MODEL}


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
