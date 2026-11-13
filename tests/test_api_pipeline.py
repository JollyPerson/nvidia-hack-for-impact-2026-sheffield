"""The web API against the real pipeline (not the mock), with the five-patient warehouse and a fake model."""

import json
import os

os.environ["ADVOLARE_MOCK_STEP_DELAY"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
from api import main  # noqa: E402
from scripts.five_patients import STUDY, build_db  # noqa: E402
from src import store  # noqa: E402
from src.llm import LLM  # noqa: E402
from tests.fake_llm import RuleBasedClient  # noqa: E402

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def real_pipeline(monkeypatch, tmp_path):
    warehouse = tmp_path / "warehouse.db"
    build_db(warehouse).close()
    monkeypatch.setattr(config, "DB_PATH", warehouse)
    monkeypatch.setattr(store, "APP_DB_PATH", tmp_path / "app.db")
    monkeypatch.setenv("ADVOLARE_MOCK", "false")
    monkeypatch.setenv("ADVOLARE_MAX_CANDIDATES", "0")
    monkeypatch.setattr(main, "make_llm", lambda: LLM(RuleBasedClient()))
    main.init_app(reset=True)
    study = store.create_study(STUDY.model_copy(update={"id": None}))
    return study.id


def screen(study_id):
    run = client.post(f"/api/studies/{study_id}/runs").json()
    with client.stream("GET", f"/api/runs/{run['run_id']}/events") as events:
        body = "".join(events.iter_text())
    return run, body


def test_real_run_streams_progress_and_saves_results(real_pipeline):
    run, body = screen(real_pipeline)
    steps = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {\"step\"")]
    assert {s["step"] for s in steps if s["state"] == "done"} == {1, 2, 3, 4, 5}
    assert any(s["state"] == "running" and "Patient 3 of 5" in s["detail"] for s in steps)
    assert "event: complete" in body

    results = client.get(f"/api/studies/{real_pipeline}/results").json()
    assert results["run_id"] == run["run_id"] and results["status"] == "complete"
    assert results["funnel"]["candidates_after_sql"] == 5 and len(results["trace"]) == 5
    by_id = {p["patient_id"]: p for p in results["patients"]}
    assert by_id["P001"]["result"]["status"] == "likely_eligible"
    assert by_id["P002"]["result"]["status"] == "excluded" and "COPD" in by_id["P002"]["summary"]
    assert by_id["P001"]["sex"] and isinstance(by_id["P001"]["age"], int)
    assert "07700" not in json.dumps(results) and "example.com" not in json.dumps(results)
    assert store.get_latest_run(real_pipeline).likely_eligible == results["funnel"]["likely_eligible"]


def test_decision_is_stored_and_contact_comes_from_patient_contacts(real_pipeline):
    screen(real_pipeline)
    url = f"/api/studies/{real_pipeline}/patients/P001"
    assert client.get(url + "/contact").status_code == 403
    out = client.post(url + "/decision", json={"decision": "approved", "note": "checked"}).json()
    assert out["patient"]["decision"] == "approved" and out["patient"]["result"]["approved"] is True
    assert out["contact"]["mobile_number"].startswith("07700 900") and out["contact"]["landline"] is None
    run_id = client.get(f"/api/studies/{real_pipeline}/results").json()["run_id"]
    assert store.get_decisions(run_id)["P001"].decision == "approved"
    actions = [a["action"] for a in client.get(f"/api/studies/{real_pipeline}/audit").json()]
    assert actions[0] == "Approved for contact"


def test_pipeline_error_is_reported_not_hung(real_pipeline, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", config.DB_PATH.parent / "missing.db")
    run, body = screen(real_pipeline)
    assert "event: failed" in body and "FileNotFoundError" in body
    assert store.get_run(run["run_id"]).status == "failed"
    assert client.get(f"/api/studies/{real_pipeline}/results").json()["funnel"] is None


def test_second_run_while_running_is_refused(real_pipeline):
    main.LIVE["x"] = main.LiveRun(run_id="x", study_id=real_pipeline)
    try:
        assert client.post(f"/api/studies/{real_pipeline}/runs").status_code == 409
    finally:
        main.LIVE.pop("x")
