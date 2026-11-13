"""API tests: the browser's contract with api/main.py, run against the mock pipeline."""
import os

os.environ["ADVOLARE_MOCK_STEP_DELAY"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import features  # noqa: E402
from api.main import app, store  # noqa: E402

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
def fresh_store(monkeypatch):
    store.reset()
    for name in ["FEATURES_ALL", *(f"FEATURE_{n.upper()}" for n in features.FEATURES)]:
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
    assert [s["short_title"] for s in studies] == ["PRISM-CAP", "HOME-COPD", "NEWS-SEP"]
    assert not any(s["screened"] for s in studies)


def test_pages_are_served():
    for path in ["/", "/studies/new", "/studies/S-001"]:
        page = client.get(path)
        assert page.status_code == 200 and "Advolare - Clinical Trial Screening System" in page.text


def test_create_study_assigns_ids_and_does_not_run():
    created = client.post("/api/studies", json=NEW_STUDY)
    assert created.status_code == 201
    study = created.json()
    assert study["id"] == "S-004"
    assert [c["id"] for c in study["criteria"]] == ["C1", "C2"]
    assert client.get(f"/api/studies/{study['id']}/results").status_code == 404


@pytest.mark.parametrize("change", [
    {"title": ""},
    {"target_sample_size": 0},
    {"recruitment_end": "2026-01-01"},
    {"criteria": []},
    {"criteria": [{"type": "inclusion", "kind": "structured", "description": "Age", "field": "age"}]},
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
    url = "/api/studies/S-001/patients/MRN-100231"
    assert client.get(url + "/contact").status_code == 403
    rejected = client.post(url + "/decision", json={"decision": "rejected"}).json()
    assert rejected["contact"] is None and client.get(url + "/contact").status_code == 403
    approved = client.post(url + "/decision", json={"decision": "approved", "note": "Checked notes"}).json()
    assert approved["patient"]["result"]["approved"] is True
    contact = client.get(url + "/contact").json()
    assert contact["mobile"].startswith("07700 900") and contact["email"].endswith("@example.com")


def test_audit_trail_records_actions():
    run_screening()
    client.post("/api/studies/S-001/patients/MRN-100517/decision", json={"decision": "rejected", "note": "Mother had COPD"})
    actions = [a["action"] for a in client.get("/api/studies/S-001/audit").json()]
    assert actions[0] == "Rejected" and "Started screening" in actions


def test_unknown_study_and_patient():
    assert client.get("/api/studies/S-999").status_code == 404
    run_screening()
    assert client.get("/api/studies/S-001/patients/MRN-000000").status_code == 404


def test_fhir_export_has_study_and_eligibility_group(all_features):
    bundle = client.get("/api/studies/S-001/fhir").json()
    study, group = (e["resource"] for e in bundle["entry"])
    assert study["resourceType"] == "ResearchStudy" and group["resourceType"] == "Group"
    assert study["recruitment"]["eligibility"]["reference"] == f"Group/{group['id']}"
    assert len(group["characteristic"]) == 7
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
    assert log.headers["content-type"].startswith("text/csv") and "MRN-100231" in log.text
    assert "07700" not in log.text


def test_letter_only_after_approval(all_features):
    run_screening()
    url = "/api/studies/S-001/patients/MRN-100231"
    assert client.get(url + "/letter").status_code == 403
    client.post(url + "/decision", json={"decision": "approved"})
    text = client.get(url + "/letter").json()["text"]
    assert text.startswith("Dear Margaret") and "pneumonia" not in text.lower()


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
    url = "/api/studies/S-001/patients/MRN-100231"
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
