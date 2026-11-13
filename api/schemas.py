"""Shapes the browser needs on top of src/models.py. API-only: nothing in the pipeline imports these.

The shared contracts (Study, Criterion, Citation, Verdict, PatientResult, ScreeningRun, Decision, AuditEntry)
come from src/models.py unchanged. The views here wrap them with what the pages show alongside.
"""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from src.models import Criterion, PatientResult


class StudySummary(BaseModel):
    id: str
    title: str
    short_title: str | None
    condition: str
    status: str
    target_sample_size: int
    screened: bool


class DecisionValue(str, Enum):
    """Values of src.models.Decision.decision. PatientResult.approved mirrors `approved`."""
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PatientView(BaseModel):
    """One row on the results page: the pipeline's PatientResult plus what the clinician needs to see alongside it.

    Age and sex come from the Patients table; the summary is built from the decide step's reasons.
    Never contains contact details.
    """
    patient_id: str
    age: int | None = None
    sex: str = ""
    summary: str = ""
    decision: DecisionValue = DecisionValue.pending
    result: PatientResult


class RemovedGroup(BaseModel):
    reason: str
    count: int


class Funnel(BaseModel):
    removed_before_screening: list[RemovedGroup] = []  # do-not-contact flags, removed by code before SQL results
    candidates_after_sql: int
    shortlisted_stage1: int
    confirmed_stage2: int
    likely_eligible: int
    needs_review: int
    excluded: int


class TraceStep(BaseModel):
    step: int
    name: str
    detail: str
    seconds: float
    model: str | None = None


class RunView(BaseModel):
    """src.models.ScreeningRun as the results page shows it: funnel counts grouped, progress steps, patient views."""
    run_id: str
    study_id: str
    status: Literal["running", "complete", "failed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    trace: list[TraceStep] = []
    funnel: Funnel | None = None
    patients: list[PatientView] = []


class Contact(BaseModel):
    """Mirrors the PatientContacts table (data/schema.sql). Never passed to a model; returned only after the
    clinician approves the patient. Every column can be empty."""
    patient_id: str
    mobile_number: str | None = None
    landline: str | None = None
    email: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None


class DraftRequest(BaseModel):
    text: str


class DraftResponse(BaseModel):
    criteria: list[Criterion]
    assumptions: list[str]


class DecisionRequest(BaseModel):
    decision: DecisionValue
    note: str = ""


class DecisionResponse(BaseModel):
    patient: PatientView
    contact: Contact | None = None
