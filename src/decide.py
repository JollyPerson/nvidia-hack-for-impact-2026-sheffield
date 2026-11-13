"""The final call, made by code: verdicts + validation flags -> likely eligible / excluded / needs review.

Rules (AGENTS.md, "Decision rules"):
- Any exclusion `met` by the models with valid citations -> excluded (with reasons).
- All inclusions `met`, no exclusions `met`, and no flags -> likely eligible.
- Anything else (any `insufficient`, any flag, any disagreement) -> needs review.

"Met by the models" means every stage that ran says `met`. Stage 2 runs only for shortlisted patients, so a
patient dropped at stage 1 has a single verdict (screening.py already checked that its citation exists, and
validation.py flags it if not). Structured criteria are checked by code in the prefilter: a structured
inclusion arrives here as a `met` stage-1 verdict with the warehouse rows as citations, and a structured
exclusion has no assessment at all because every candidate passed it in SQL.

Notes that never mention an exclusion (decision by the team, 13 Nov): when BOTH models were shown every
note of the patient and both said they found nothing relevant (screening.ALL_NOTES_READ), the exclusion
counts as "not mentioned in the notes" rather than unknown, and the reason says so. A model error, an
uncertain note, a citation or any flag still sends the patient to needs review.

The LLMs never make this call. This module never removes a verdict or a flag; it only sets `status` and
adds plain-English `reasons` for the clinician.
"""

from __future__ import annotations

from src.screening import ALL_NOTES_READ
from src.models import (
    Criterion,
    CriterionAssessment,
    CriterionKind,
    CriterionType,
    FinalStatus,
    FlagType,
    PatientResult,
    Study,
    VerdictValue,
)


def _criterion_id(c: Criterion, index: int) -> str:
    return c.id or f"C{index + 1}"  # same rule as screening.py and prefilter.py


def _verdicts(a: CriterionAssessment) -> list[VerdictValue]:
    return [v.verdict for v in (a.stage1, a.stage2) if v is not None]


def _agreed(a: CriterionAssessment | None, value: VerdictValue) -> bool:
    """True when at least one stage ran and every stage that ran gave `value`."""
    verdicts = _verdicts(a) if a else []
    return bool(verdicts) and all(v == value for v in verdicts)


def _not_mentioned(a: CriterionAssessment) -> bool:
    """Both models read every note and found nothing: the notes never mention it."""
    stages = [a.stage1, a.stage2]
    return all(v is not None and v.verdict == VerdictValue.insufficient and not v.citations
               and v.reasoning.startswith(ALL_NOTES_READ) for v in stages) and not a.flags


def _citations_ok(a: CriterionAssessment) -> bool:
    return not any(f.type == FlagType.citation_failed for f in a.flags)


def decide(study: Study, result: PatientResult) -> PatientResult:
    """Set `result.status` and add the reasons for it. Returns the same object."""
    by_id = {a.criterion_id: a for a in result.assessments}
    excluded_by: list[str] = []
    review: list[str] = []
    not_mentioned: list[str] = []

    for i, c in enumerate(study.criteria):
        a = by_id.get(_criterion_id(c, i))

        if c.type == CriterionType.exclusion:
            if a is None:
                if c.kind == CriterionKind.notes:
                    review.append(f"Not checked: {c.description}")
                continue  # structured exclusion: passed in SQL
            if _agreed(a, VerdictValue.met) and _citations_ok(a):
                excluded_by.append(c.description)
            elif c.kind == CriterionKind.notes and _not_mentioned(a):
                not_mentioned.append(c.description)
            elif not _agreed(a, VerdictValue.not_met):
                review.append(_why_review(c, a))
            elif a.flags:
                review.append(_why_review(c, a))
        else:
            if a is None:
                review.append(f"Not checked: {c.description}")
            elif not _agreed(a, VerdictValue.met) or a.flags:
                review.append(_why_review(c, a))

    if excluded_by:
        result.status = FinalStatus.excluded
        result.reasons += [f"Excluded: {d}" for d in excluded_by]
    elif review:
        result.status = FinalStatus.needs_review
        result.reasons += review
    else:
        result.status = FinalStatus.likely_eligible
        result.reasons.append(f"All {len(study.criteria)} criteria checked: every inclusion met, no exclusion met")
    if result.status != FinalStatus.excluded:
        result.reasons += [f"Not mentioned in the notes (both models read every note): {d}" for d in not_mentioned]
    return result


def _why_review(c: Criterion, a: CriterionAssessment) -> str:
    flags = {f.type for f in a.flags}
    if FlagType.citation_failed in flags:
        return f"Evidence could not be checked: {c.description}"
    if FlagType.stage_disagreement in flags:
        return f"Models disagree: {c.description}"
    verdicts = _verdicts(a)
    if VerdictValue.insufficient in verdicts:
        return f"Not enough in the records: {c.description}"
    if c.type == CriterionType.inclusion and VerdictValue.not_met in verdicts:
        return f"Inclusion not met: {c.description}"
    return f"Check: {c.description}"
