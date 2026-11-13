"""The agentic funnel (see AGENTS.md). Owns: tool loop, stage 1 (shortlist), stage 2 (verification).

Each model conversation answers ONE criterion for ONE patient:
  1. tool loop: the model calls search_notes / get_labs / ... (hard step limit)
  2. verdict: the model returns JSON matching models.Verdict (enforced in llm.py)

Only `notes` criteria are sent to the models. `structured` criteria are already
checked by code in prefilter.py before a patient reaches this module.

Stage 1 (screener) is tuned for recall: a patient is dropped only when an
exclusion criterion is `met` with a citation. Everything else goes to stage 2.
Stage 2 (verifier, a different model family) re-reads the ORIGINAL records. It
is given stage 1's pointers (which records to look at) but never stage 1's
verdict or reasoning.

This module does not make the final eligibility call: validation.py and
decide.py do that from the PatientResult returned here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date

import openai

import config
from src.llm import LLM, PromptTooLong
from src.models import (
    Citation,
    Criterion,
    CriterionAssessment,
    CriterionKind,
    CriterionType,
    PatientResult,
    Study,
    Verdict,
    VerdictValue,
)
from src.tools import TOOL_SCHEMAS, PatientTools

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You check ONE clinical-trial criterion for ONE hospital patient, using only the patient's records.
Today is {today}. Patient: sex {sex}, date of birth {dob}.

Use the tools to find evidence: search_notes (free-text notes), get_labs, get_observations, get_diagnoses.
Search with several different keywords, synonyms and abbreviations (e.g. "home oxygen", "LTOT", "O2").
Watch for negation ("no", "denies", "not on"), uncertainty ("?", "query", "possible") and dates.
When you have enough evidence, or have searched and found none, stop calling tools and reply "done".
{role_note}"""

SCREENER_NOTE = "Be thorough: missing a real match is worse than flagging one for review."

VERIFIER_NOTE = (
    "You are an independent second reviewer. Read the records yourself and reach your own conclusion. "
    "Records another reviewer pointed to may be wrong or irrelevant."
)

VERDICT_INSTRUCTIONS = """Now give your verdict on the criterion as ONE JSON object and nothing else:
{{"reasoning": "<one or two sentences>",
 "citations": [{{"table": "<e.g. ClinicalNotes>", "record_id": "<the row's ID, e.g. NoteID>", "field": "<column name, e.g. NoteText>", "quote": "<exact text copied from that field, or the exact value>"}}],
 "basis": "direct_evidence" | "nothing_found",
 "verdict": "met" | "not_met" | "insufficient"}}

Rules:
- met = a record directly shows the criterion is TRUE for this patient.
- not_met = a record directly shows the criterion is FALSE, e.g. "no history of COPD", "not on home oxygen",
  "CXR: clear lung fields". A record that simply does not mention the topic is NOT evidence of not_met.
- insufficient = no record directly answers it: nothing relevant found, only unrelated notes, conflicting,
  or uncertain ("?", "query", "possible", "awaited"). Absence of evidence is insufficient. Never guess.
- basis = "direct_evidence" only if a cited quote itself states the answer; otherwise "nothing_found".
- {type_rule}
- met and not_met MUST cite at least one record you actually saw. Copy quotes exactly; do not paraphrase.
- Only cite records returned by the tools or shown above."""

TYPE_RULES = {
    CriterionType.inclusion: "This is an INCLUSION criterion: met means the patient qualifies on it.",
    CriterionType.exclusion: "This is an EXCLUSION criterion: met means the patient HAS the excluding "
    "condition (so would be excluded).",
}


def _today() -> str:
    return config.REFERENCE_DATE or date.today().isoformat()


def _criterion_id(c: Criterion, index: int) -> str:
    return c.id or f"C{index + 1}"


def _pointer_block(tools: PatientTools, pointers: list[Citation]) -> str:
    """The ORIGINAL records stage 1 cited, fetched fresh from the warehouse by code."""
    seen, records = set(), []
    for p in pointers:
        key = (p.table, p.record_id)
        if key in seen:
            continue
        seen.add(key)
        rec = tools.get_record(p.table, p.record_id)
        if rec is not None:
            records.append(rec)
    if not records:
        return ""
    return "\nRecords another reviewer pointed to (original text from the warehouse):\n" + "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records
    )


def screen_criterion(
    llm: LLM,
    tools: PatientTools,
    criterion: Criterion,
    role: str,
    pointers: list[Citation] | None = None,
    audit: list | None = None,
) -> Verdict:
    """Run the tool loop + verdict for one criterion and one patient. Always returns a Verdict."""
    summary = tools.patient_summary()
    system = SYSTEM_PROMPT.format(
        today=_today(),
        sex=summary.get("Sex", "unknown"),
        dob=summary.get("DateOfBirth", "unknown"),
        role_note=SCREENER_NOTE if role == "screener" else VERIFIER_NOTE,
    )
    user = f"Criterion ({criterion.type.value}): {criterion.description}"
    if pointers:
        user += _pointer_block(tools, pointers)
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    tool_calls_made = 0
    try:
        for _step in range(config.MAX_TOOL_STEPS):
            msg = llm.chat(role, messages, tools=TOOL_SCHEMAS)
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            for tc in calls:
                tool_calls_made += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tools.run(tc["function"]["name"], tc["function"]["arguments"]),
                    }
                )
        messages.append(
            {"role": "user", "content": VERDICT_INSTRUCTIONS.format(type_rule=TYPE_RULES[criterion.type])}
        )
        verdict = llm.verdict(role, messages)
    except PromptTooLong as e:
        verdict = Verdict(verdict=VerdictValue.insufficient, reasoning=f"prompt too long: {e}")
    except openai.OpenAIError as e:
        log.error("model server error (%s, %s): %s", role, tools.patient_id, e)
        verdict = Verdict(verdict=VerdictValue.insufficient, reasoning=f"model server error: {type(e).__name__}")

    if audit is not None:
        audit.append(
            {
                "role": role,
                "model": config.MODELS[role],
                "patient_id": tools.patient_id,
                "criterion": criterion.description,
                "tool_calls": tool_calls_made,
                "messages": messages,
                "verdict": verdict.model_dump(mode="json"),
            }
        )
    return verdict


def _excluded_by(v: Verdict | None, c: Criterion) -> bool:
    return c.type == CriterionType.exclusion and v is not None and v.verdict == VerdictValue.met and bool(v.citations)


def screen_patient(
    llm: LLM,
    conn: sqlite3.Connection,
    study: Study,
    patient_id: str,
    audit: list | None = None,
) -> PatientResult:
    """Stage 1 then (if shortlisted) stage 2 for every notes criterion of the study."""
    tools = PatientTools(conn, patient_id)
    criteria = [(i, c) for i, c in enumerate(study.criteria) if c.kind == CriterionKind.notes]
    # Exclusions first, so a clear exclusion can stop stage 1 early (if configured).
    criteria.sort(key=lambda ic: ic[1].type != CriterionType.exclusion)

    assessments = {_criterion_id(c, i): CriterionAssessment(criterion_id=_criterion_id(c, i)) for i, c in criteria}
    result = PatientResult(study_id=study.id or "", patient_id=patient_id)

    # Stage 1: screener
    for i, c in criteria:
        a = assessments[_criterion_id(c, i)]
        a.stage1 = screen_criterion(llm, tools, c, "screener", audit=audit)
        if _excluded_by(a.stage1, c):
            result.reasons.append(f"Stage 1: exclusion met: {c.description}")
            if config.STAGE1_STOP_ON_EXCLUSION:
                break
    result.shortlisted = not result.reasons

    # Stage 2: verifier, only for shortlisted patients
    if result.shortlisted:
        for i, c in criteria:
            a = assessments[_criterion_id(c, i)]
            pointers = a.stage1.citations if a.stage1 else []
            a.stage2 = screen_criterion(llm, tools, c, "verifier", pointers=pointers, audit=audit)

    # Keep the study's own criterion order in the output.
    result.assessments = [assessments[_criterion_id(c, i)] for i, c in sorted(criteria)]
    return result


def screen_study(
    llm: LLM,
    conn: sqlite3.Connection,
    study: Study,
    patient_ids: list[str],
    audit: list | None = None,
) -> list[PatientResult]:
    """Screen every candidate patient from the prefilter."""
    results = []
    for n, pid in enumerate(patient_ids, 1):
        log.info("screening patient %s (%d/%d)", pid, n, len(patient_ids))
        results.append(screen_patient(llm, conn, study, pid, audit=audit))
    return results
