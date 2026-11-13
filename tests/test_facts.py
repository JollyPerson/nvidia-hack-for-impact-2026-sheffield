"""Pre-extracted patient facts (src/facts.py, data/extract_facts.py), with a fake model (no Spark needed)."""

import json
import re
from types import SimpleNamespace

import pytest

import config
from data import extract_facts
from scripts.five_patients import EXPECTED, STUDY, build_db
from src import facts
from src.decide import decide
from src.llm import LLM
from src.models import Criterion, FinalStatus, VerdictValue
from src.screening import screen_patient
from tests.fake_llm import response

# Keyword rules a fake extractor applies to each note: fact -> [(value, regex)], first match wins.
RULES = {
    "pneumonia_radiological": [("uncertain", r"\?subtle LLL shadowing"), ("yes", r"CXR: \w+ \w+ (lobe|zone) consolidation")],
    "copd_home_oxygen": [("no", r"NOT on home oxygen"), ("yes", r"on home O2"), ("no", r"No known lung disease"),
                         ("no", r"no other resp hx")],
}


class FakeExtractor:
    """Answers the extraction prompt with RULES. quote_override lets a test make the model invent a quote."""

    def __init__(self, quote_override=None):
        self.requests = []
        self.quote_override = quote_override
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        note = kwargs["messages"][-1]["content"].split("Clinical note:\n", 1)[1]
        out = {}
        for f in facts.FACTS:
            out[f.key] = {"value": "not_mentioned", "quote": ""}
            for value, pattern in RULES.get(f.key, []):
                m = re.search(pattern, note)
                if m:
                    out[f.key] = {"value": value, "quote": self.quote_override or m.group(0)}
                    break
        return response(content=json.dumps(out))


class NoModel:
    """Fails the test if screening calls a model."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        raise AssertionError("screening called a model for a criterion covered by facts")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "warehouse.db"
    build_db(path).close()
    return path


def _conn(path):
    import sqlite3
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


def test_ingest_then_screen_without_any_model_call(db):
    fake = FakeExtractor()
    counts = extract_facts.run(db, llm=LLM(fake), workers=3, log=lambda *_: None)
    assert len(fake.requests) == 6  # one call per note (6 notes), not per criterion
    assert counts[facts.ERROR] == counts[facts.UNVERIFIED] == 0
    conn = _conn(db)
    for pid, expected in EXPECTED.items():
        r = screen_patient(LLM(NoModel()), conn, STUDY, pid)
        got = {a.criterion_id: a.stage1.verdict.value for a in r.assessments}
        assert got == expected, pid
        assert all(a.stage2 is None and facts.FACTS_TAG in a.stage1.reasoning for a in r.assessments)
        for a in r.assessments:  # every citation is a real quote from that patient's note
            for c in a.stage1.citations:
                text = conn.execute("SELECT NoteText FROM ClinicalNotes WHERE NoteID=?", (c.record_id,)).fetchone()[0]
                assert c.quote in text
    conn.close()


def test_decide_uses_fact_verdicts(db):
    extract_facts.run(db, llm=LLM(FakeExtractor()), log=lambda *_: None)
    conn = _conn(db)
    status = {pid: decide(STUDY, screen_patient(LLM(NoModel()), conn, STUDY, pid)).status for pid in EXPECTED}
    conn.close()
    assert status["P001"] == FinalStatus.likely_eligible
    assert status["P002"] == FinalStatus.excluded
    assert status["P003"] == FinalStatus.needs_review  # uncertain X-ray
    assert status["P004"] == FinalStatus.likely_eligible
    assert status["P005"] == FinalStatus.needs_review  # inclusion not mentioned


def test_rerun_only_extracts_missing_notes(db):
    extract_facts.run(db, llm=LLM(FakeExtractor()), limit=2, log=lambda *_: None)
    fake = FakeExtractor()
    extract_facts.run(db, llm=LLM(fake), log=lambda *_: None)
    assert len(fake.requests) == 4


def test_invented_quote_is_unverified_and_falls_back_to_live(db):
    extract_facts.run(db, llm=LLM(FakeExtractor(quote_override="patient on home oxygen 24/7")), log=lambda *_: None)
    conn = _conn(db)
    assert conn.execute("SELECT COUNT(*) FROM PatientFacts WHERE Value='unverified'").fetchone()[0] > 0
    crit = STUDY.criteria[1]  # COPD on home oxygen
    assert facts.fact_verdict(conn, "P002", crit) is None  # screened live instead
    # P005's single note mentions neither fact, so it is still answered by code.
    assert facts.fact_verdict(conn, "P005", crit).verdict == VerdictValue.insufficient
    conn.close()


def test_falls_back_when_not_extracted_or_not_in_fact_list_or_switched_off(db, monkeypatch):
    conn = _conn(db)
    crit = STUDY.criteria[1]
    assert facts.fact_verdict(conn, "P001", crit) is None  # no PatientFacts table yet
    conn.close()
    extract_facts.run(db, llm=LLM(FakeExtractor()), log=lambda *_: None)
    conn = _conn(db)
    other = Criterion(type="exclusion", kind="notes", description="Pregnant or breastfeeding")
    assert facts.fact_verdict(conn, "P001", other) is None
    assert facts.fact_verdict(conn, "P001", crit).verdict == VerdictValue.not_met
    monkeypatch.setattr(config, "FACTS_ENABLED", False)
    assert facts.fact_verdict(conn, "P001", crit) is None
    conn.close()


def test_every_seeded_notes_criterion_is_in_the_fact_list():
    from data.seed_studies import DEMO_STUDIES
    missing = [(s.short_title, c.description) for s in DEMO_STUDIES for c in s.criteria
               if c.kind == "notes" and facts.fact_for(c) is None]
    assert not missing


def test_parse_extraction_checks_quotes():
    note = "PMH: COPD, on home O2 2L."
    got = facts.parse_extraction(json.dumps({
        "copd_home_oxygen": {"value": "yes", "quote": "on  HOME o2"},
        "immunosuppressed": {"value": "yes", "quote": "on prednisolone 40mg"},
        "acs_cause": {"value": "maybe", "quote": ""},
    }), note)
    assert got["copd_home_oxygen"][0] == "yes"
    assert got["immunosuppressed"][0] == facts.UNVERIFIED
    assert got["acs_cause"][0] == facts.ERROR
    assert got["hf_iv_diuretics"][0] == facts.ERROR  # missing from the reply


def test_cache_reloads_facts_into_a_rebuilt_warehouse(db, tmp_path):
    cache = tmp_path / "facts_cache.json"
    extract_facts.run(db, llm=LLM(FakeExtractor()), log=lambda *_: None, cache=cache)
    fresh = tmp_path / "rebuilt.db"
    build_db(fresh).close()
    assert extract_facts.load_cache(fresh, cache) == 6
    conn = _conn(fresh)
    assert facts.fact_verdict(conn, "P002", STUDY.criteria[1]).verdict == VerdictValue.met
    conn.close()
