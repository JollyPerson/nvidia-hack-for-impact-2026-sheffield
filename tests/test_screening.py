"""Tests for llm.py, tools.py and screening.py, using a fake model (no Spark needed)."""

import json

import httpx2 as httpx
import openai
import pytest

import config
from scripts.five_patients import CONTACTS, EXPECTED, STUDY, build_db
from src import llm as llm_mod
from src.llm import LLM, fit_to_budget, parse_verdict
from src.models import VerdictValue
from src.screening import screen_criterion, screen_patient, screen_study
from src.tools import PatientTools
from tests.fake_llm import RuleBasedClient, ScriptedClient, response


@pytest.fixture
def conn():
    c = build_db()
    yield c
    c.close()


GOOD = json.dumps(
    {"verdict": "met", "reasoning": "x",
     "citations": [{"table": "ClinicalNotes", "record_id": "N-1", "field": "NoteText", "quote": "q"}]}
)

# ---------------------------------------------------------------- five-patient test


def test_five_patients_end_to_end(conn):
    results = {r.patient_id: r for r in screen_study(LLM(RuleBasedClient()), conn, STUDY, list(EXPECTED))}
    for pid, expected in EXPECTED.items():
        r = results[pid]
        for a in r.assessments:
            want = expected[a.criterion_id]
            assert a.stage1 is not None, (pid, a.criterion_id)
            assert a.stage1.verdict.value == want, (pid, a.criterion_id)
    # P002's exclusion is met in stage 1 -> not shortlisted, never reaches the verifier.
    assert not results["P002"].shortlisted
    assert all(a.stage2 is None for a in results["P002"].assessments)
    assert results["P002"].reasons
    # Everyone else (incl. insufficient) goes on to stage 2.
    for pid in ("P001", "P003", "P004", "P005"):
        assert results[pid].shortlisted
        assert all(a.stage2 is not None for a in results[pid].assessments)


def test_models_come_from_config(conn):
    fake = RuleBasedClient()
    screen_patient(LLM(fake), conn, STUDY, "P001")
    used = {r["model"] for r in fake.requests}
    assert used == {config.SCREENER_MODEL, config.VERIFIER_MODEL}
    assert all(r["temperature"] == 0 and r["seed"] == config.SEED for r in fake.requests)


def test_contacts_never_sent_to_model(conn):
    fake = RuleBasedClient()
    screen_study(LLM(fake), conn, STUDY, list(EXPECTED))
    sent = json.dumps([r["messages"] for r in fake.requests])
    for _pid, mobile, _l, email, _g, _gp in CONTACTS:
        assert mobile not in sent and email not in sent


def test_stage2_gets_pointers_but_not_stage1_reasoning(conn):
    fake = RuleBasedClient()
    screen_patient(LLM(fake), conn, STUDY, "P001")
    verifier_first = [r for r in fake.requests if r["model"] == config.VERIFIER_MODEL][0]
    user = verifier_first["messages"][1]["content"]
    assert "N-100" in user                      # the cited record, fetched fresh
    assert "consolidation on CXR" not in user   # stage 1's reasoning text is not passed on
    assert '"verdict"' not in user


def test_structured_criteria_not_sent_to_model(conn):
    from src.models import Criterion

    study = STUDY.model_copy(deep=True)
    study.criteria.append(Criterion(id="C3", type="inclusion", kind="structured",
                                    description="Age >= 18", field="age", operator=">=", value=18))
    r = screen_patient(LLM(RuleBasedClient()), conn, study, "P001")
    assert {a.criterion_id for a in r.assessments} == {"C1", "C2"}


# ---------------------------------------------------------------- tool loop


def test_tool_loop_has_hard_step_limit(conn, monkeypatch):
    monkeypatch.setattr(config, "MAX_TOOL_STEPS", 3)
    always_tools = [response(tool_calls=[("get_labs", {})]) for _ in range(3)] + [response(content=GOOD)]
    fake = ScriptedClient(always_tools)
    v = screen_criterion(LLM(fake), PatientTools(conn, "P001"), STUDY.criteria[0], "screener")
    assert v.verdict == VerdictValue.met
    assert len(fake.requests) == 4           # 3 tool steps + 1 verdict call
    assert "tools" not in fake.requests[-1]  # verdict call is JSON-only


def test_server_error_gives_insufficient(conn):
    req = httpx.Request("POST", "http://x")
    fake = ScriptedClient([openai.APIConnectionError(request=req)])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P001"), STUDY.criteria[0], "screener")
    assert v.verdict == VerdictValue.insufficient


# ---------------------------------------------------------------- llm.py


def test_invalid_then_valid_retries_once():
    fake = ScriptedClient([response(content="not json"), response(content=GOOD)])
    v = LLM(fake).verdict("screener", [{"role": "user", "content": "x"}])
    assert v.verdict == VerdictValue.met and len(fake.requests) == 2


def test_invalid_twice_returns_insufficient():
    no_cite = json.dumps({"verdict": "met", "reasoning": "x", "citations": []})
    fake = ScriptedClient([response(content="oops"), response(content=no_cite)])
    v = LLM(fake).verdict("screener", [{"role": "user", "content": "x"}])
    assert v.verdict == VerdictValue.insufficient and len(fake.requests) == 2


def test_verdict_uses_json_schema():
    fake = ScriptedClient([response(content=GOOD)])
    LLM(fake).verdict("verifier", [{"role": "user", "content": "x"}])
    assert fake.requests[0]["response_format"]["type"] == "json_schema"


def test_nothing_found_is_forced_to_insufficient():
    # What both real models did on P005: not_met, citing an unrelated note.
    text = json.dumps({"reasoning": "no mention of COPD", "basis": "nothing_found", "verdict": "not_met",
                       "citations": [{"table": "ClinicalNotes", "record_id": "N-5001", "field": "NoteText",
                                      "quote": "Pt settled overnight"}]})
    v = parse_verdict(text)
    assert v.verdict == VerdictValue.insufficient
    assert v.citations  # evidence kept for the clinician to see
    assert "code:" in v.reasoning


def test_direct_evidence_keeps_verdict():
    data = json.loads(GOOD) | {"basis": "direct_evidence"}
    assert parse_verdict(json.dumps(data)).verdict == VerdictValue.met


def test_stop_on_exclusion_flag(conn, monkeypatch):
    monkeypatch.setattr(config, "STAGE1_STOP_ON_EXCLUSION", True)
    r = screen_patient(LLM(RuleBasedClient()), conn, STUDY, "P002")
    assert {a.criterion_id: a.stage1 for a in r.assessments}["C1"] is None


def _cite(table, rid, field, quote):
    return {"table": table, "record_id": rid, "field": field, "quote": quote}


def test_not_met_from_structured_absence_becomes_insufficient(conn):
    # What qwen3.6 did on P005 C2: not_met citing diagnoses + observations only.
    bad = json.dumps({"reasoning": "COPD not in diagnoses; obs on room air", "basis": "direct_evidence",
                      "verdict": "not_met",
                      "citations": [_cite("Diagnoses", "D-5001", "Description", "Other abnormalities of gait"),
                                    _cite("Observations", "O-5001", "OnOxygen", "0")]})
    fake = ScriptedClient([response(content="done"), response(content=bad)])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P005"), STUDY.criteria[1], "verifier")
    assert v.verdict == VerdictValue.insufficient and v.citations


def test_not_met_with_note_is_kept(conn):
    ok = json.dumps({"reasoning": "explicitly not on home O2", "basis": "direct_evidence", "verdict": "not_met",
                     "citations": [_cite("ClinicalNotes", "N-4001", "NoteText", "NOT on home oxygen")]})
    fake = ScriptedClient([response(content="done"), response(content=ok)])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P004"), STUDY.criteria[1], "verifier")
    assert v.verdict == VerdictValue.not_met


def test_invented_exclusion_evidence_does_not_drop_patient(conn):
    def excl(quote, rid="N-1001"):
        return response(content=json.dumps({"reasoning": "on home O2", "basis": "direct_evidence", "verdict": "met",
                                            "citations": [_cite("ClinicalNotes", rid, "NoteText", quote)]}))

    study = STUDY.model_copy(update={"criteria": [STUDY.criteria[1]]})
    done, insuff = response(content="done"), response(content=json.dumps(
        {"reasoning": "x", "basis": "nothing_found", "verdict": "insufficient", "citations": []}))
    # quote is not in P001's note -> not dropped, verifier runs
    r = screen_patient(LLM(ScriptedClient([done, excl("home O2 2L via concentrator"), done, insuff])),
                       conn, study, "P001")
    assert r.shortlisted and r.assessments[0].stage2 is not None
    # record belongs to another patient -> not dropped either
    r = screen_patient(LLM(ScriptedClient([done, excl("home O2 2L", rid="N-2001"), done, insuff])),
                       conn, study, "P001")
    assert r.shortlisted
    # real quote (different case/spacing) -> dropped
    r = screen_patient(LLM(ScriptedClient([done, excl("on HOME o2  2L", rid="N-2001")])), conn, study, "P002")
    assert not r.shortlisted


def test_pointer_window_is_centred_on_the_quote(conn):
    long_text = "filler. " * 400 + "Pt on home O2 2L overnight. " + "more. " * 50
    conn.execute("INSERT INTO ClinicalNotes VALUES ('N-9','E-101','P001','ward round','FY1','2026-11-12',?)",
                 (long_text,))
    rec = PatientTools(conn, "P001").get_record("ClinicalNotes", "N-9", around="home O2 2L")
    assert "home O2 2L" in rec["NoteText"]


def test_table_names_are_matched_loosely(conn):
    t = PatientTools(conn, "P001")
    assert t.get_record("clinicalnote", "N-1001")["table"] == "ClinicalNotes"
    assert t.citation_checks_out("PATHOLOGY", "L-1001", "Value", "148")
    assert not t.citation_checks_out("PatientContacts", "P001", "Email", "example.com")


def test_unexpected_error_gives_insufficient(conn):
    fake = ScriptedClient([RuntimeError("boom")])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P001"), STUDY.criteria[0], "screener")
    assert v.verdict == VerdictValue.insufficient


def test_verdict_schema_puts_verdict_last():
    assert list(llm_mod.VERDICT_SCHEMA["properties"])[-1] == "verdict"


def test_parse_verdict_handles_fences_and_think():
    v = parse_verdict("<think>hmm {</think>```json\n" + GOOD + "\n```")
    assert v.verdict == VerdictValue.met


def test_reasoning_effort_is_never_dropped():
    """Without reasoning_effort both models think by default (Qwen can return nothing), so a server that
    rejects it is an error for that call, not a reason to stop sending it."""
    req = httpx.Request("POST", "http://x")
    err = openai.BadRequestError("unknown field reasoning_effort", response=httpx.Response(400, request=req), body=None)
    fake = ScriptedClient([err, response(content=GOOD)])
    llm = LLM(fake)
    with pytest.raises(openai.BadRequestError):
        llm.verdict("screener", [{"role": "user", "content": "x"}])
    llm.verdict("screener", [{"role": "user", "content": "x"}])
    assert all(r["reasoning_effort"] == "none" for r in fake.requests)


def test_reasoning_can_be_switched_on_per_role(monkeypatch):
    monkeypatch.setitem(config.REASONING, "verifier", True)
    fake = ScriptedClient([response(content=GOOD), response(content=GOOD)])
    llm = LLM(fake)
    llm.verdict("screener", [{"role": "user", "content": "x"}])
    llm.verdict("verifier", [{"role": "user", "content": "x"}])
    screener, verifier = fake.requests
    assert screener["reasoning_effort"] == "none" and screener["max_tokens"] == config.MAX_OUTPUT_TOKENS
    assert "reasoning_effort" not in verifier and verifier["max_tokens"] == config.REASONING_MAX_OUTPUT_TOKENS


def test_reasoning_override_wins_over_config(monkeypatch):
    monkeypatch.setitem(config.REASONING, "screener", True)
    monkeypatch.setattr(llm_mod, "REASONING_OVERRIDES", {"screener": False})
    assert llm_mod.reasoning_on("screener") is False


def test_fit_to_budget_truncates_tool_results_only():
    huge = "x" * 100_000
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "crit"},
            {"role": "tool", "tool_call_id": "1", "content": huge}]
    out = fit_to_budget(msgs)
    assert llm_mod.estimate_tokens(out) <= config.CONTEXT_TOKENS - config.MAX_OUTPUT_TOKENS
    assert out[0] == msgs[0] and out[1] == msgs[1]
    assert out[2]["content"].endswith(llm_mod.TRUNCATED)
    assert msgs[2]["content"] == huge  # original untouched


def test_prompt_too_long_without_tool_results_gives_insufficient():
    v = LLM(ScriptedClient([])).verdict("screener", [{"role": "user", "content": "x" * 100_000}])
    assert v.verdict == VerdictValue.insufficient


# ---------------------------------------------------------------- tools.py


def test_search_notes_is_scoped_to_patient(conn):
    out = json.loads(PatientTools(conn, "P001").search_notes("home O2 COPD consolidation"))
    assert out["notes"] and all(n["NoteID"].startswith("N-1") for n in out["notes"])


def test_search_notes_no_match_says_so(conn):
    out = json.loads(PatientTools(conn, "P005").search_notes("consolidation"))
    assert out["notes"] == [] and "message" in out


def test_long_note_is_cut_around_match(conn, monkeypatch):
    long_text = "filler. " * 500 + "CXR: dense LUL consolidation. " + "more. " * 500
    conn.execute("INSERT INTO ClinicalNotes VALUES ('N-9','E-101','P001','ward round','FY1','2026-11-12',?)",
                 (long_text,))
    note = next(n for n in json.loads(PatientTools(conn, "P001").search_notes("LUL"))["notes"]
                if n["NoteID"] == "N-9")
    assert len(note["NoteText"]) <= config.MAX_NOTE_CHARS + 2
    assert "dense LUL consolidation" in note["NoteText"]


def test_get_record_refuses_other_patient_and_contacts(conn):
    t = PatientTools(conn, "P001")
    assert t.get_record("ClinicalNotes", "N-1001")["NoteID"] == "N-1001"
    assert t.get_record("ClinicalNotes", "N-2001") is None   # belongs to P002
    assert t.get_record("PatientContacts", "P001") is None


def test_bad_tool_calls_return_errors_not_exceptions(conn):
    t = PatientTools(conn, "P001")
    assert "error" in json.loads(t.run("get_medications", "{}"))
    assert "error" in json.loads(t.run("get_labs", "{not json"))
    assert json.loads(t.run("get_labs", '{"test": "CRP"}'))["rows"][0]["PathologyID"] == "L-1001"
    assert json.loads(t.run("get_diagnoses", None))["rows"]
    assert json.loads(t.run("get_observations", "{}"))["rows"][0]["ObservationID"] == "O-1001"


def test_criteria_in_parallel_give_the_same_verdicts():
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "w.db"
        build_db(path).close()
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        from src.pipeline import _SharedConn
        for pid in EXPECTED:
            one = screen_patient(LLM(RuleBasedClient()), conn, STUDY, pid)
            many = screen_patient(LLM(RuleBasedClient()), _SharedConn(conn), STUDY, pid, workers=4)
            assert many.model_dump() == one.model_dump(), pid
        conn.close()


def test_notes_are_in_the_first_prompt(conn):
    fake = ScriptedClient([response(content="done"), response(content=GOOD)])
    screen_criterion(LLM(fake), PatientTools(conn, "P002"), STUDY.criteria[1], "screener")
    first = fake.requests[0]["messages"]
    user = next(m["content"] for m in first if m["role"] == "user")
    system = first[0]["content"]
    assert "Clinical notes for this patient" in user and "home O2" in user.replace("oxygen", "O2")
    assert "shown in full" in system
    assert len(fake.requests) == 2  # read the notes, said done, gave the verdict: no tool calls needed
    assert not any(c[1] in user for c in CONTACTS if c[1])  # contact details never reach a model


def test_notes_in_prompt_can_be_switched_off(conn, monkeypatch):
    monkeypatch.setattr(config, "NOTES_IN_PROMPT_CHARS", 0)
    fake = ScriptedClient([response(content="done"), response(content=GOOD)])
    screen_criterion(LLM(fake), PatientTools(conn, "P002"), STUDY.criteria[1], "screener")
    user = next(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user")
    assert "Clinical notes for this patient" not in user
    assert "search_notes" in fake.requests[0]["messages"][0]["content"]


def test_notes_in_prompt_respect_the_limit(conn, monkeypatch):
    monkeypatch.setattr(config, "NOTES_IN_PROMPT_CHARS", 60)  # smaller than any one note
    fake = ScriptedClient([response(content="done"), response(content=GOOD)])
    screen_criterion(LLM(fake), PatientTools(conn, "P002"), STUDY.criteria[1], "screener")
    user = next(m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user")
    assert "Clinical notes for this patient" not in user


NOTHING = json.dumps({"verdict": "insufficient", "reasoning": "no mention", "citations": [], "basis": "nothing_found"})


def test_nothing_found_after_all_notes_is_marked(conn):
    from src.screening import ALL_NOTES_READ
    fake = ScriptedClient([response(content="done"), response(content=NOTHING)])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P005"), STUDY.criteria[1], "screener")
    assert v.verdict == VerdictValue.insufficient and v.reasoning.startswith(ALL_NOTES_READ)


def test_nothing_found_without_all_notes_is_not_marked(conn, monkeypatch):
    from src.screening import ALL_NOTES_READ
    monkeypatch.setattr(config, "NOTES_IN_PROMPT_CHARS", 0)
    fake = ScriptedClient([response(content="done"), response(content=NOTHING)])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P005"), STUDY.criteria[1], "screener")
    assert not v.reasoning.startswith(ALL_NOTES_READ)


def test_one_call_check_when_every_note_is_shown(conn, monkeypatch):
    monkeypatch.setattr(config, "ONE_CALL_CHECKS", True)
    fake = ScriptedClient([response(content=NOTHING)])
    v = screen_criterion(LLM(fake), PatientTools(conn, "P005"), STUDY.criteria[1], "screener")
    assert len(fake.requests) == 1 and "tools" not in fake.requests[0]
    assert "Coded diagnoses for this patient" in fake.requests[0]["messages"][1]["content"]
    assert v.verdict == VerdictValue.insufficient


def test_tool_loop_when_notes_are_not_all_shown(conn, monkeypatch):
    monkeypatch.setattr(config, "ONE_CALL_CHECKS", True)
    monkeypatch.setattr(config, "NOTES_IN_PROMPT_CHARS", 0)
    fake = ScriptedClient([response(content="done"), response(content=NOTHING)])
    screen_criterion(LLM(fake), PatientTools(conn, "P005"), STUDY.criteria[1], "screener")
    assert len(fake.requests) == 2
