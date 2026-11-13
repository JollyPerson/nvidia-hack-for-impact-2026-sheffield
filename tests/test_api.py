"""API tests: the browser's contract with api/main.py, run against the mock pipeline (ADVOLARE_MOCK=true).

tests/test_api_pipeline.py runs the same endpoints against the real pipeline with a fake model.
"""
import os

os.environ["ADVOLARE_MOCK_STEP_DELAY"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import features  # noqa: E402
from api import main  # noqa: E402
from api.main import app  # noqa: E402
from src import store  # noqa: E402

client = TestClient(app)

NEW_STUDY = {
    "title": "Test study", "condition": "Pneumonia", "study_design": "Observational cohort",
    "intervention": "None", "primary_outcome": "Readmission", "target_sample_size": 10,
    "recruitment_start": "2026-11-01", "recruitment_end": "2027-11-01", "chief_investigator": "Dr Test",
    "sponsor": "Test sponsor", "ethics_reference": "IRAS 000000 (mock)",
    "criteria": [
        {"type": "inclusion", "kind": "structured", "description": "Aged 16 or over", "field": "age", "operator": ">=", "value": 16},
        {"type": "exclusion", "kind": "notes", "description": "COPD on home oxygen"},
    ],
}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "APP_DB_PATH", tmp_path / "app.db")
    monkeypatch.setenv("ADVOLARE_MOCK", "true")
    main.init_app(reset=True)
    features.clear_overrides()
    for name in ["FEATURES_ALL", "ADVOLARE_DEV_MODE", *(f"FEATURE_{n.upper()}" for n in features.FEATURES)]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def all_features(monkeypatch):
    monkeypatch.setenv("FEATURES_ALL", "true")


def run_screening(study_id="S-001"):
    run = client.post(f"/api/studies/{study_id}/runs").json()
    with client.stream("GET", f"/api/runs/{run['run_id']}/events") as events:
        body = "".join(events.iter_text())
    return run, body


def test_lists_studies():
    studies = client.get("/api/studies").json()
    assert [s["short_title"] for s in studies] == ["PRISM-CAP", "HOMESTEP-HF"]  # data/seed_studies.py
    assert not any(s["screened"] for s in studies)


def test_pages_are_served():
    for path in ["/", "/studies/new", "/studies/S-001"]:
        page = client.get(path)
        assert page.status_code == 200 and "Advolare - Clinical Trial Screening System" in page.text


def test_create_study_assigns_ids_and_does_not_run():
    created = client.post("/api/studies", json=NEW_STUDY)
    assert created.status_code == 201
    study = created.json()
    assert study["id"] == "S-003"
    assert [c["id"] for c in study["criteria"]] == ["C1", "C2"]
    assert client.get(f"/api/studies/{study['id']}/results").status_code == 404


@pytest.mark.parametrize("change", [
    {"title": ""},
    {"target_sample_size": 0},
    {"recruitment_end": "2026-01-01"},
    {"criteria": []},
    {"criteria": [{"type": "inclusion", "kind": "structured", "description": "Age", "field": "age"}]},
    {"criteria": [{"type": "inclusion", "kind": "structured", "description": "SpO2", "field": "spo2", "operator": ">=", "value": 90}]},
    {"criteria": [{"type": "inclusion", "kind": "structured", "description": "Age", "field": "age", "operator": ">=", "value": "old"}]},
])
def test_create_study_rejects_invalid(change):
    assert client.post("/api/studies", json={**NEW_STUDY, **change}).status_code == 422


def test_draft_criteria_from_free_text():
    text = client.get("/api/criteria/example").json()["text"]
    draft = client.post("/api/criteria/draft", json={"text": text}).json()
    kinds = [(c["type"], c["kind"]) for c in draft["criteria"]]
    assert kinds[0] == ("inclusion", "structured")
    assert ("exclusion", "notes") in kinds
    assert draft["assumptions"]


def test_run_streams_every_step_then_results():
    run, body = run_screening()
    assert run["status"] == "running"
    assert body.count("event: step") == 10 and "event: complete" in body
    results = client.get("/api/studies/S-001/results").json()
    assert results["status"] == "complete"
    assert results["funnel"]["likely_eligible"] == 3
    statuses = {p["result"]["status"] for p in results["patients"]}
    assert statuses == {"likely_eligible", "needs_review", "excluded"}


def test_results_never_contain_contact_details():
    run_screening()
    text = client.get("/api/studies/S-001/results").text
    assert "07700" not in text and "example.com" not in text


def test_contact_only_after_approval():
    run_screening()
    url = "/api/studies/S-001/patients/MRN100231"
    assert client.get(url + "/contact").status_code == 403
    rejected = client.post(url + "/decision", json={"decision": "rejected"}).json()
    assert rejected["contact"] is None and client.get(url + "/contact").status_code == 403
    approved = client.post(url + "/decision", json={"decision": "approved", "note": "Checked notes"}).json()
    assert approved["patient"]["result"]["approved"] is True
    contact = client.get(url + "/contact").json()
    assert contact["mobile_number"].startswith("07700 900") and contact["email"].endswith("@example.com")


def test_audit_trail_records_actions():
    run_screening()
    client.post("/api/studies/S-001/patients/MRN100517/decision", json={"decision": "rejected", "note": "Mother had COPD"})
    actions = [a["action"] for a in client.get("/api/studies/S-001/audit").json()]
    assert actions[0] == "Rejected" and any(a.startswith("Started screening") for a in actions)


def test_unknown_study_and_patient():
    assert client.get("/api/studies/S-999").status_code == 404
    run_screening()
    assert client.get("/api/studies/S-001/patients/MRN000000").status_code == 404


def test_fhir_export_has_study_and_eligibility_group(all_features):
    bundle = client.get("/api/studies/S-001/fhir").json()
    study, group = (e["resource"] for e in bundle["entry"])
    assert study["resourceType"] == "ResearchStudy" and group["resourceType"] == "Group"
    assert study["recruitment"]["eligibility"]["reference"] == f"Group/{group['id']}"
    assert len(group["characteristic"]) == 9
    assert [c["exclude"] for c in group["characteristic"]].count(True) == 4


def test_what_if_reuses_verdicts(all_features):
    run_screening()
    rows = {r["criterion_id"]: r for r in client.get("/api/studies/S-001/what-if").json()}
    assert rows["C5"]["likely_eligible_without"] > rows["C5"]["likely_eligible_now"]  # negation trap + home O2
    assert rows["C2"]["extra_candidates_to_screen"] > 0


def test_fairness_and_screening_log(all_features):
    run_screening()
    fair = client.get("/api/studies/S-001/fairness").json()
    assert fair["shortlist_size"] == 6 and fair["flags"]
    log = client.get("/api/studies/S-001/screening-log.csv")
    assert log.headers["content-type"].startswith("text/csv") and "MRN100231" in log.text
    assert "07700" not in log.text


def test_letter_only_after_approval(all_features):
    run_screening()
    url = "/api/studies/S-001/patients/MRN100231"
    assert client.get(url + "/letter").status_code == 403
    client.post(url + "/decision", json={"decision": "approved"})
    text = client.get(url + "/letter").json()["text"]
    assert text.startswith("Dear [patient name]") and "pneumonia" not in text.lower()


# ---------- Feature flags ----------
EXTRA_ROUTES = {
    "fairness": "/api/studies/S-001/fairness",
    "what_if": "/api/studies/S-001/what-if",
    "screening_log": "/api/studies/S-001/screening-log.csv",
    "fhir_export": "/api/studies/S-001/fhir",
}


def test_default_flags():
    assert client.get("/api/features").json() == {
        "do_not_contact": True, "draft_criteria": True, "fairness": False, "what_if": False,
        "screening_log": False, "invitation_letter": False, "fhir_export": False}


@pytest.mark.parametrize("name,url", EXTRA_ROUTES.items())
def test_extra_routes_404_when_off_and_200_when_on(monkeypatch, name, url):
    run_screening()
    assert client.get(url).status_code == 404
    monkeypatch.setenv(f"FEATURE_{name.upper()}", "true")
    assert client.get(url).status_code == 200


def test_one_flag_overrides_features_all(monkeypatch):
    monkeypatch.setenv("FEATURES_ALL", "true")
    monkeypatch.setenv("FEATURE_FAIRNESS", "false")
    flags = client.get("/api/features").json()
    assert flags["what_if"] and not flags["fairness"]
    monkeypatch.setenv("FEATURES_ALL", "false")
    assert not any(client.get("/api/features").json().values())


def test_empty_or_unknown_values_keep_the_default(monkeypatch):
    monkeypatch.setenv("FEATURE_DO_NOT_CONTACT", "")
    monkeypatch.setenv("FEATURE_FAIRNESS", "maybe")
    flags = client.get("/api/features").json()
    assert flags["do_not_contact"] and not flags["fairness"]


def test_letter_off_even_after_approval(monkeypatch):
    run_screening()
    url = "/api/studies/S-001/patients/MRN100231"
    client.post(url + "/decision", json={"decision": "approved"})
    assert client.get(url + "/letter").status_code == 404
    assert client.get(url + "/contact").status_code == 200  # contact after approval is core, never switched off


def test_draft_criteria_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FEATURE_DRAFT_CRITERIA", "false")
    assert client.post("/api/criteria/draft", json={"text": "adults"}).status_code == 404
    assert client.get("/api/criteria/example").status_code == 404


def test_do_not_contact_flag_controls_funnel(monkeypatch):
    run_screening()
    assert client.get("/api/studies/S-001/results").json()["funnel"]["removed_before_screening"]
    monkeypatch.setenv("FEATURE_DO_NOT_CONTACT", "false")
    run_screening()
    assert client.get("/api/studies/S-001/results").json()["funnel"]["removed_before_screening"] == []


# ---------- Developer mode toggles ----------
def test_dev_toggle_overrides_env_and_resets(monkeypatch):
    run_screening()
    assert client.get("/api/studies/S-001/fairness").status_code == 404
    rows = client.post("/api/dev/features", json={"name": "fairness", "enabled": True}).json()
    assert {r["name"]: r["source"] for r in rows}["fairness"] == "developer toggle"
    assert client.get("/api/features").json()["fairness"] is True
    assert client.get("/api/studies/S-001/fairness").status_code == 200
    monkeypatch.setenv("FEATURE_FAIRNESS", "false")  # toggle still wins over the env flag
    assert client.get("/api/studies/S-001/fairness").status_code == 200
    client.post("/api/dev/features", json={"name": "fairness", "enabled": None})
    assert client.get("/api/studies/S-001/fairness").status_code == 404
    client.post("/api/dev/features", json={"name": "fhir_export", "enabled": True})
    client.delete("/api/dev/features")
    assert client.get("/api/features").json()["fhir_export"] is False


def test_dev_toggle_rejects_unknown_feature():
    assert client.post("/api/dev/features", json={"name": "nope", "enabled": True}).status_code == 422


def test_dev_mode_off_hides_toggles_and_ignores_overrides(monkeypatch):
    client.post("/api/dev/features", json={"name": "what_if", "enabled": True})
    monkeypatch.setenv("ADVOLARE_DEV_MODE", "false")
    assert client.get("/api/dev/features").status_code == 404
    assert client.post("/api/dev/features", json={"name": "what_if", "enabled": True}).status_code == 404
    assert client.get("/api/features").json()["what_if"] is False


def test_dev_reasoning_toggle(monkeypatch):
    from src import llm as llm_mod
    monkeypatch.setattr(llm_mod, "REASONING_OVERRIDES", {})
    rows = {m["role"]: m for m in client.get("/api/dev/models").json()}
    assert rows["screener"]["reasoning"] is False and rows["verifier"]["reasoning"] is False
    rows = {m["role"]: m for m in client.post("/api/dev/models", json={"role": "verifier", "reasoning": True}).json()}
    assert rows["verifier"]["reasoning"] is True and rows["verifier"]["source"] == "developer toggle"
    assert rows["verifier"]["max_output_tokens"] > rows["screener"]["max_output_tokens"]
    assert client.get("/api/health").json()["reasoning"] == {"screener": False, "verifier": True}
    client.delete("/api/dev/features")
    assert client.get("/api/health").json()["reasoning"]["verifier"] is False
    assert client.post("/api/dev/models", json={"role": "nope", "reasoning": True}).status_code == 422
    monkeypatch.setenv("ADVOLARE_DEV_MODE", "false")
    assert client.get("/api/dev/models").status_code == 404


@pytest.mark.parametrize("raw,want", [(None, 5), ("", 5), ("  ", 5), ("3", 3), ("0", None)])
def test_max_candidates_empty_env_keeps_default(monkeypatch, raw, want):
    """docker-compose passes ADVOLARE_MAX_CANDIDATES as an empty string when it is not set."""
    if raw is None:
        monkeypatch.delenv("ADVOLARE_MAX_CANDIDATES", raising=False)
    else:
        monkeypatch.setenv("ADVOLARE_MAX_CANDIDATES", raw)
    assert main.max_candidates() == want


def test_dev_limit_overrides_env_until_reset(monkeypatch):
    monkeypatch.setattr(main, "LIMIT_OVERRIDE", {})
    monkeypatch.setenv("ADVOLARE_MAX_CANDIDATES", "2")
    assert client.get("/api/dev/limit").json()["patients"] == 2
    assert client.post("/api/dev/limit", json={"patients": 5}).json()["patients"] == 5
    assert main.max_candidates() == 5
    client.post("/api/dev/limit", json={"patients": 0})
    assert main.max_candidates() is None
    client.delete("/api/dev/features")
    assert main.max_candidates() == 2
    assert client.post("/api/dev/limit", json={"patients": -1}).status_code == 422


def test_demo_study_criteria_refresh_on_start(tmp_path):
    """An app.db kept across deploys picks up changed demo criteria (PRISM-CAP gained C8/C9)."""
    from data import seed_studies
    db = tmp_path / "kept.db"
    seed_studies.seed(db)
    old = store.get_study("S-001", db=db)
    store.replace_criteria("S-001", old.criteria[:3], db=db)
    seed_studies.seed(db)
    assert [c.id for c in store.get_study("S-001", db=db).criteria] == [c.id for c in old.criteria]


def test_dev_verifier_toggle(monkeypatch):
    from src import llm as llm_mod
    monkeypatch.setattr(llm_mod, "STAGE_OVERRIDES", {})
    monkeypatch.setattr(main.config, "VERIFIER_ENABLED", True)
    assert client.get("/api/dev/verifier").json()["enabled"] is True
    assert client.post("/api/dev/verifier", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/api/health").json()["verifier"] is False
    client.delete("/api/dev/features")
    assert client.get("/api/dev/verifier").json()["enabled"] is True
