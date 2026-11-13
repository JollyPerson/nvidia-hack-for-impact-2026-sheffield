"""Shared data contracts for Advolare.

Every module imports its shapes from here so they cannot drift apart.
Do not change this file without explicit approval (see AGENTS.md).
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StudyStatus(str, Enum):
    recruiting = "recruiting"
    closed = "closed"


class CriterionType(str, Enum):
    inclusion = "inclusion"
    exclusion = "exclusion"


class CriterionKind(str, Enum):
    structured = "structured"  # checkable from a field by code (SQL / scores)
    notes = "notes"  # needs free-text reasoning by the LLMs


class Operator(str, Enum):
    lt = "<"
    le = "<="
    eq = "=="
    ne = "!="
    ge = ">="
    gt = ">"


class VerdictValue(str, Enum):
    met = "met"
    not_met = "not_met"
    insufficient = "insufficient"


class FlagType(str, Enum):
    citation_failed = "citation_failed"
    stage_disagreement = "stage_disagreement"


class FinalStatus(str, Enum):
    likely_eligible = "likely_eligible"
    needs_review = "needs_review"
    excluded = "excluded"


# ---------------------------------------------------------------------------
# Study and criteria
# ---------------------------------------------------------------------------


class Criterion(BaseModel):
    """One inclusion or exclusion criterion.

    Structured criteria carry field / operator / value (and an optional time
    window) so code can turn them into parameterised SQL. Notes criteria carry
    only the free-text description, which the LLMs reason over.
    """

    id: str | None = None  # assigned by code when the study is saved
    type: CriterionType
    description: str = Field(min_length=1)
    kind: CriterionKind

    # Structured criteria only
    field: str | None = None  # e.g. "age", "curb65", "egfr"
    operator: Operator | None = None
    value: float | str | None = None
    time_window_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _structured_needs_field_operator_value(self) -> Criterion:
        if self.kind == CriterionKind.structured:
            missing = [
                name
                for name in ("field", "operator", "value")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    f"structured criterion is missing: {', '.join(missing)}"
                )
        return self


class Study(BaseModel):
    """A clinical study, modelled on ISRCTN / HRA study summaries."""

    id: str | None = None  # assigned by code when the study is saved
    title: str = Field(min_length=1)
    short_title: str | None = None  # acronym
    condition: str = Field(min_length=1)
    study_design: str  # e.g. "randomised controlled trial", "observational"
    phase: str | None = None
    intervention: str
    primary_outcome: str
    target_sample_size: int = Field(gt=0)
    recruitment_start: date
    recruitment_end: date
    chief_investigator: str
    sponsor: str
    ethics_reference: str  # REC / IRAS
    status: StudyStatus = StudyStatus.recruiting
    criteria: list[Criterion] = Field(default_factory=list)

    @property
    def inclusion_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.type == CriterionType.inclusion]

    @property
    def exclusion_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.type == CriterionType.exclusion]

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Study:
        if self.recruitment_end < self.recruitment_start:
            raise ValueError("recruitment_end is before recruitment_start")
        return self


# ---------------------------------------------------------------------------
# Model output
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A pointer to one specific warehouse record that supports a verdict."""

    table: str  # e.g. "ClinicalNotes"
    record_id: str  # e.g. "N-1042"
    field: str  # e.g. "NoteText"
    quote: str  # exact text or value from that field


class Verdict(BaseModel):
    """One model's answer to ONE criterion for ONE patient.

    This is the JSON shape every LLM call must return.
    """

    verdict: VerdictValue
    reasoning: str
    citations: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _met_or_not_met_needs_citation(self) -> Verdict:
        # `insufficient` may have no citation: it is also the fallback code
        # returns when a model fails to produce valid output twice.
        if self.verdict != VerdictValue.insufficient and not self.citations:
            raise ValueError(f"a '{self.verdict.value}' verdict needs a citation")
        return self


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class Flag(BaseModel):
    """Added by the validation layer. Flags never remove a patient."""

    type: FlagType
    stage: int | None = Field(default=None, ge=1, le=2)  # None = across stages
    detail: str = ""


class CriterionAssessment(BaseModel):
    """Both models' verdicts on one criterion, plus any validation flags."""

    criterion_id: str
    stage1: Verdict | None = None  # screener
    stage2: Verdict | None = None  # verifier; None if not shortlisted
    flags: list[Flag] = Field(default_factory=list)


class PatientResult(BaseModel):
    """Screening outcome for one candidate patient in one study.

    Never contains contact details: those are joined from PatientContacts
    only after the clinician approves the patient.
    """

    study_id: str
    patient_id: str  # LocalPatientIdentifier (MRN)
    shortlisted: bool = False  # passed stage 1
    assessments: list[CriterionAssessment] = Field(default_factory=list)
    status: FinalStatus = FinalStatus.needs_review
    reasons: list[str] = Field(default_factory=list)  # e.g. exclusion reasons
    approved: bool = False
