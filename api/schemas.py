"""Shapes the browser needs on top of src/models.py. API-only: nothing in the pipeline imports these.

The shared contracts (Study, Criterion, Citation, Verdict, PatientResult) come from src/models.py unchanged.
"""
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


class Decision(str, Enum):
    """The clinician's decision. PatientResult.approved mirrors `approved`; `rejected` is kept here for the log."""
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PatientView(BaseModel):
    """One row on the results page: the pipeline's PatientResult plus what the clinician needs to see alongside it.

    Age and sex come from the Patients table. Never contains contact details.
    """
    patient_id: str
    age: int
    sex: str
    summary: str
    decision: Decision = Decision.pending
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


class ScreeningRun(BaseModel):
    run_id: str
    study_id: str
    status: Literal["running", "complete"]
    trace: list[TraceStep] = []
    funnel: Funnel | None = None
    patients: list[PatientView] = []


class Contact(BaseModel):
    """From PatientContacts. Never passed to a model; returned only after the clinician approves the patient."""
    patient_id: str
    name: str
    mobile: str
    email: str
    guardian_name: str | None = None
    guardian_phone: str | None = None


class DraftRequest(BaseModel):
    text: str


class DraftResponse(BaseModel):
    criteria: list[Criterion]
    assumptions: list[str]


class DecisionRequest(BaseModel):
    decision: Decision
    note: str = ""


class DecisionResponse(BaseModel):
    patient: PatientView
    contact: Contact | None = None


class AuditEntry(BaseModel):
    when: str
    study_id: str
    who: str
    action: str
    patient_id: str = ""
    note: str = ""
