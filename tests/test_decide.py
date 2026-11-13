"""Tests for src/decide.py: the final call is made by code from verdicts and flags (AGENTS.md decision rules)."""

from datetime import date

import pytest

from src.decide import decide
from src.models import (Citation, Criterion, CriterionAssessment, FinalStatus, Flag, FlagType, PatientResult, Study,
                        Verdict)

CITE = [Citation(table="ClinicalNotes", record_id="N1", field="NoteText", quote="CXR consolidation")]
STUDY = Study(
    id="S-T", title="t", condition="c", study_design="d", intervention="i", primary_outcome="o",
    target_sample_size=1, recruitment_start=date(2026, 1, 1), recruitment_end=date(2027, 1, 1),
    chief_investigator="x", sponsor="x", ethics_reference="x",
    criteria=[
        Criterion(id="C1", type="inclusion", kind="structured", description="Aged 16 or over", field="age",
                  operator=">=", value=16),
        Criterion(id="C2", type="inclusion", kind="notes", description="Pneumonia on CXR"),
        Criterion(id="C3", type="exclusion", kind="notes", description="COPD on home oxygen"),
        Criterion(id="C4", type="exclusion", kind="structured", description="eGFR below 30", field="egfr",
                  operator="<", value=30),
    ],
)


def v(verdict):
    return Verdict(verdict=verdict, reasoning="r", citations=[] if verdict == "insufficient" else CITE)


def a(cid, s1, s2=None, flags=()):
    return CriterionAssessment(criterion_id=cid, stage1=v(s1), stage2=v(s2) if s2 else None, flags=list(flags))


def result(c2=("met", "met"), c3=("not_met", "not_met"), flags2=(), flags3=(), shortlisted=True):
    return PatientResult(study_id="S-T", patient_id="P1", shortlisted=shortlisted, assessments=[
        a("C1", "met"), a("C2", *c2, flags=flags2), a("C3", *c3, flags=flags3)])


FAILED = Flag(type=FlagType.citation_failed, stage=2, detail="quote does not match the record")
DISAGREE = Flag(type=FlagType.stage_disagreement, detail="screener and verifier disagree")


def test_all_met_no_flags_is_likely_eligible():
    r = decide(STUDY, result())
    assert r.status == FinalStatus.likely_eligible and r.reasons


def test_exclusion_met_by_both_models_is_excluded_with_reason():
    r = decide(STUDY, result(c3=("met", "met")))
    assert r.status == FinalStatus.excluded
    assert "Excluded: COPD on home oxygen" in r.reasons


def test_exclusion_met_at_stage_1_only_run_is_excluded():
    """Stage 2 never runs for a patient dropped at stage 1, so the single verdict decides."""
    r = PatientResult(study_id="S-T", patient_id="P1", shortlisted=False,
                      assessments=[a("C1", "met"), a("C2", "met"), a("C3", "met")])
    assert decide(STUDY, r).status == FinalStatus.excluded


def test_exclusion_with_failed_citation_needs_review():
    r = decide(STUDY, result(c3=("met", "met"), flags3=[FAILED]))
    assert r.status == FinalStatus.needs_review
    assert any("could not be checked" in x for x in r.reasons)


@pytest.mark.parametrize("kwargs", [
    {"c2": ("met", "insufficient")},
    {"c2": ("insufficient", "insufficient")},
    {"c2": ("met", "not_met"), "flags2": [DISAGREE]},
    {"c3": ("not_met", "met"), "flags3": [DISAGREE]},
    {"flags2": [FAILED]},
    {"c2": ("not_met", "not_met")},
])
def test_anything_else_needs_review(kwargs):
    assert decide(STUDY, result(**kwargs)).status == FinalStatus.needs_review


def test_missing_notes_assessment_needs_review():
    r = PatientResult(study_id="S-T", patient_id="P1", shortlisted=True, assessments=[a("C1", "met"), a("C2", "met", "met")])
    r = decide(STUDY, r)
    assert r.status == FinalStatus.needs_review and "Not checked: COPD on home oxygen" in r.reasons


def test_decide_never_removes_verdicts_or_flags():
    before = result(c2=("met", "not_met"), flags2=[DISAGREE])
    after = decide(STUDY, before.model_copy(deep=True))
    assert after.assessments == before.assessments
