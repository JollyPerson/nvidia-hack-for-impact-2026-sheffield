"""eval/evaluate.py end to end with a fake model (no Spark needed)."""

import json

from eval import evaluate
from eval.cases import CASES, build_db
from src.llm import LLM
from tests.fake_llm import ScriptedClient, response


class AlwaysInsufficient(ScriptedClient):
    def __init__(self):
        super().__init__([])

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if "tools" in kwargs:
            return response(content="done")
        return response(content=json.dumps({"reasoning": "nothing", "citations": [], "basis": "nothing_found",
                                            "verdict": "insufficient"}))


def test_every_case_runs_and_metrics_compute(tmp_path):
    db = str(tmp_path / "eval.db")
    build_db(db).close()
    llm = LLM(AlwaysInsufficient())
    rows = [evaluate.run_case(llm, db, c) for c in CASES]
    assert len(rows) == len(CASES)
    m = evaluate.metrics(rows)
    # A model that never commits excludes no one and marks no one eligible.
    assert m["eligible_wrongly_excluded"] == [] and m["ineligible_marked_eligible"] == []
    assert m["recall_incl_review"].endswith("100%")
    assert "Per criterion" in evaluate.report(rows, m)


def test_metrics_counts():
    rows = [
        {"pid": "A", "study": "PNEUMONIA", "eligible": True, "status": "likely_eligible",
         "criteria": {"C3": {"label": "met", "final": "met", "stage1": "met"}}},
        {"pid": "B", "study": "PNEUMONIA", "eligible": False, "status": "likely_eligible",
         "criteria": {"C3": {"label": "not_met", "final": "met", "stage1": "met"}}},
        {"pid": "C", "study": "PNEUMONIA", "eligible": True, "status": "excluded",
         "criteria": {"C3": {"label": "met", "final": "met", "stage1": "met"}}},
    ]
    m = evaluate.metrics(rows)
    assert m["recall"] == "1/2 = 50%" and m["precision"] == "1/2 = 50%"
    assert m["eligible_wrongly_excluded"] == ["C"] and m["ineligible_marked_eligible"] == ["B"]
    assert m["criterion_success_overall"] == "2/3 = 67%"
