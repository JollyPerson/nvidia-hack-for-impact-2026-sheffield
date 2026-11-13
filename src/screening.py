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
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import openai

import config
from src.llm import LLM, NOTHING_FOUND, PromptTooLong, max_output_tokens, verifier_on
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
from src.tools import TOOL_SCHEMAS, PatientTools, resolve_table

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You check ONE clinical-trial criterion for ONE hospital patient, using only the patient's records.
Today is {today}. Patient: sex {sex}, date of birth {dob}.

{notes_note}
Use the tools to find evidence: search_notes (free-text notes), get_labs, get_observations, get_diagnoses.
Search with several different keywords, synonyms and abbreviations (e.g. "home oxygen", "LTOT", "O2").
Watch for negation ("no", "denies", "not on"), uncertainty ("?", "query", "possible") and dates.
When you have enough evidence, or have searched and found none, stop calling tools and reply "done".
{role_note}"""

SCREENER_NOTE = "Be thorough: missing a real match is worse than flagging one for review."

VERIFIER_NOTE = (
    "You are an independent second reviewer. Read the records yourself and reach your own conclusion. "
    "Records another reviewer pointed to may be wrong or irrelevant. "
    "Be strict: if no record explicitly answers the criterion, the answer is insufficient."
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
- A missing diagnosis code, normal vital signs, or "on room air" during this admission do NOT show that a
  condition or treatment is absent. not_met needs a NOTE that explicitly says so.
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


NOTES_SHOWN = (
    "The patient's clinical notes are shown in full in the user message. Read them first. If they answer the "
    "criterion, reply \"done\" straight away; call tools only for labs, observations, diagnoses{more}."
)
NOTES_NOT_SHOWN = "The patient's clinical notes are not shown: find them with search_notes."


# Marks an `insufficient` verdict reached after the model was shown EVERY note of the patient and said it
# found nothing relevant. decide.py may then treat an exclusion as not mentioned (rather than unknown).
ALL_NOTES_READ = "[code: all notes read, nothing found]"


def _notes_block(tools: PatientTools, role: str) -> tuple[str, str]:
    """The patient's notes, newest first and whole, within the prompt budget. Returns (block, system note).

    Read by code from the warehouse for this patient only (never PatientContacts)."""
    limit = config.NOTES_IN_PROMPT_CHARS
    if limit <= 0:
        return "", NOTES_NOT_SHOWN
    # Leave room for the system prompt, tool schemas, the verdict instructions and a few tool turns.
    room = int((config.CONTEXT_TOKENS - max_output_tokens(role)) * config.CHARS_PER_TOKEN) - 9000
    limit = min(limit, max(room, 0))
    rows = tools.conn.execute(
        "SELECT NoteID, NoteType, CreatedDateTime, NoteText FROM ClinicalNotes "
        "WHERE LocalPatientIdentifier = ? ORDER BY CreatedDateTime DESC",
        (tools.patient_id,),
    ).fetchall()
    shown, used = [], 0
    for r in rows:
        line = json.dumps({"table": "ClinicalNotes", "NoteID": r["NoteID"], "NoteType": r["NoteType"],
                           "CreatedDateTime": r["CreatedDateTime"], "NoteText": r["NoteText"]},
                          ensure_ascii=False, separators=(",", ":"))
        if used + len(line) > limit:
            break
        shown.append(line)
        used += len(line)
    if not rows:
        return "\nClinical notes for this patient: none recorded.", NOTES_SHOWN.format(more="")
    if not shown:
        return "", NOTES_NOT_SHOWN
    hidden = len(rows) - len(shown)
    more = f", or the {hidden} older notes not shown (search_notes)" if hidden else ""
    header = f"\nClinical notes for this patient ({len(shown)} of {len(rows)}, newest first, full text):\n"
    return header + "\n".join(shown), NOTES_SHOWN.format(more=more)


def _pointer_block(tools: PatientTools, pointers: list[Citation]) -> str:
    """The ORIGINAL records stage 1 cited, fetched fresh from the warehouse by code."""
    seen, records = set(), []
    for p in pointers:
        key = (p.table, p.record_id)
        if key in seen:
            continue
        seen.add(key)
        rec = tools.get_record(p.table, p.record_id, around=p.quote)
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
    notes, notes_note = _notes_block(tools, role)
    all_notes_shown = notes_note.startswith(NOTES_SHOWN[:40]) and "older notes not shown" not in notes_note
    system = SYSTEM_PROMPT.format(
        today=_today(),
        sex=summary.get("Sex", "unknown"),
        dob=summary.get("DateOfBirth", "unknown"),
        notes_note=notes_note,
        role_note=SCREENER_NOTE if role == "screener" else VERIFIER_NOTE,
    )
    user = f"Criterion ({criterion.type.value}): {criterion.description}" + notes
    if pointers:
        user += _pointer_block(tools, pointers)
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    tool_calls_made = 0
    # Every note is already in the prompt: add the coded diagnoses too and ask for the verdict in ONE call,
    # with no tool loop (config.ONE_CALL_CHECKS). Labs and observations are structured criteria (SQL).
    one_call = config.ONE_CALL_CHECKS and all_notes_shown
    if one_call:
        messages[1]["content"] += "\nCoded diagnoses for this patient: " + tools.get_diagnoses()
    try:
        for _step in range(0 if one_call else config.MAX_TOOL_STEPS):
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
        verdict = enforce_negative_evidence(llm.verdict(role, messages))
        if (all_notes_shown and verdict.verdict == VerdictValue.insufficient and not verdict.citations
                and NOTHING_FOUND in verdict.reasoning):
            verdict = verdict.model_copy(update={"reasoning": f"{ALL_NOTES_READ} {verdict.reasoning}"})
    except PromptTooLong as e:
        verdict = Verdict(verdict=VerdictValue.insufficient, reasoning=f"prompt too long: {e}")
    except openai.OpenAIError as e:
        log.error("model server error (%s, %s): %s", role, tools.patient_id, e)
        verdict = Verdict(verdict=VerdictValue.insufficient, reasoning=f"model server error: {type(e).__name__}")
    except Exception as e:  # noqa: BLE001 - one odd reply must not stop a whole screening run
        log.exception("unexpected error (%s, %s)", role, tools.patient_id)
        verdict = Verdict(verdict=VerdictValue.insufficient, reasoning=f"screening error: {type(e).__name__}")

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


def enforce_negative_evidence(v: Verdict) -> Verdict:
    """Code rule: a negative needs an explicit statement in a clinical note.

    For a notes criterion, `not_met` must cite at least one ClinicalNotes record.
    A condition missing from the coded diagnoses, or normal observations, never
    proves it absent, so such a verdict becomes `insufficient`.
    """
    if v.verdict != VerdictValue.not_met or any(
        resolve_table(c.table) == "ClinicalNotes" for c in v.citations
    ):
        return v
    return Verdict(
        verdict=VerdictValue.insufficient,
        reasoning="[code: not_met cited no clinical note, so -> insufficient] " + v.reasoning,
        citations=v.citations,
    )


def _excluded_by(v: Verdict | None, c: Criterion, tools: PatientTools) -> bool:
    """Stage 1 drops a patient only on an exclusion that is `met` AND backed by at
    least one citation that really exists in this patient's records. An exclusion
    resting on invented evidence goes to stage 2 instead."""
    if c.type != CriterionType.exclusion or v is None or v.verdict != VerdictValue.met:
        return False
    return any(tools.citation_checks_out(ct.table, ct.record_id, ct.field, ct.quote) for ct in v.citations)


def screen_patient(
    llm: LLM,
    conn: sqlite3.Connection,
    study: Study,
    patient_id: str,
    audit: list | None = None,
    workers: int = 1,
) -> PatientResult:
    """Stage 1 then (if shortlisted) stage 2 for every notes criterion of the study.

    workers > 1 checks that many criteria at once in each stage (the model server takes several requests
    at a time). The connection is then used from several threads, so it must be safe for that (pipeline.py
    passes one that serialises each query); it is only read. Stage 1 then checks every criterion, so
    STAGE1_STOP_ON_EXCLUSION has no effect.
    """
    tools = PatientTools(conn, patient_id)
    criteria = [(i, c) for i, c in enumerate(study.criteria) if c.kind == CriterionKind.notes]
    # Exclusions first, so a clear exclusion can stop stage 1 early (if configured).
    criteria.sort(key=lambda ic: ic[1].type != CriterionType.exclusion)

    assessments = {_criterion_id(c, i): CriterionAssessment(criterion_id=_criterion_id(c, i)) for i, c in criteria}
    result = PatientResult(study_id=study.id or "", patient_id=patient_id)
    parallel = workers > 1 and len(criteria) > 1

    # Pre-extracted facts (src/facts.py): criteria answered by code from PatientFacts, no model call.
    # They skip both model stages; the rest are screened live below.
    from src.facts import fact_verdict  # facts.py imports this module

    live = []
    for i, c in criteria:
        v = fact_verdict(conn, patient_id, c)
        if v is None:
            live.append((i, c))
            continue
        assessments[_criterion_id(c, i)].stage1 = v
        if _excluded_by(v, c, tools):
            result.reasons.append(f"Exclusion met (pre-extracted facts): {c.description}")
    criteria_all, criteria = criteria, live
    parallel = workers > 1 and len(criteria) > 1

    # Stage 1: screener
    if not criteria:
        pass
    elif parallel:
        verdicts = _in_parallel(workers, [lambda c=c: screen_criterion(llm, tools, c, "screener", audit=audit)
                                          for _, c in criteria])
        for (i, c), v in zip(criteria, verdicts):
            assessments[_criterion_id(c, i)].stage1 = v
            if _excluded_by(v, c, tools):
                result.reasons.append(f"Stage 1: exclusion met: {c.description}")
    else:
        for i, c in criteria:
            a = assessments[_criterion_id(c, i)]
            a.stage1 = screen_criterion(llm, tools, c, "screener", audit=audit)
            if _excluded_by(a.stage1, c, tools):
                result.reasons.append(f"Stage 1: exclusion met: {c.description}")
                if config.STAGE1_STOP_ON_EXCLUSION:
                    break
    result.shortlisted = not result.reasons

    # Stage 2: verifier, only for shortlisted patients (and not in single-model mode)
    if result.shortlisted and verifier_on() and criteria:
        def verify(i, c):
            a = assessments[_criterion_id(c, i)]
            pointers = a.stage1.citations if a.stage1 else []
            return screen_criterion(llm, tools, c, "verifier", pointers=pointers, audit=audit)

        if parallel:
            verdicts = _in_parallel(workers, [lambda i=i, c=c: verify(i, c) for i, c in criteria])
        else:
            verdicts = [verify(i, c) for i, c in criteria]
        for (i, c), v in zip(criteria, verdicts):
            assessments[_criterion_id(c, i)].stage2 = v

    # Keep the study's own criterion order in the output.
    result.assessments = [assessments[_criterion_id(c, i)] for i, c in sorted(criteria_all)]

    return result


def _in_parallel(workers: int, calls: list) -> list:
    """Run the calls on up to `workers` threads; results in the same order as `calls`."""
    with ThreadPoolExecutor(max_workers=min(workers, len(calls)), thread_name_prefix="criterion") as pool:
        return list(pool.map(lambda f: f(), calls))


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
