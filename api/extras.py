"""Extras on top of the core flow: do-not-contact, what-if, fairness, invitation letter, FHIR export, screening log.

Pure functions over the run and study. Endpoints live in api/main.py. All data is synthetic.
"""
import csv
import io

from api.schemas import PatientView
from src.models import FinalStatus, Study, VerdictValue

# ---------- Do-not-contact: removed by code before any screening, never shown to a model ----------
DO_NOT_CONTACT = [
    ("National data opt-out registered", 2),
    ("Asked not to be contacted about research (coded in record)", 1),
    ("Recorded as deceased", 1),
]

# ---------- SNOMED CT concepts (illustrative: check in the UK SNOMED CT browser before real use) ----------
SNOMED_BY_FIELD = {"age": ("424144002", "Current chronological age"), "egfr": ("80274001", "Glomerular filtration rate"),
                   "curb65": ("", "CURB-65 score"), "news2": ("1104051000000101", "Royal College of Physicians NEWS2 score"),
                   "crp": ("55235003", "C-reactive protein measurement"), "spo2": ("431314004", "Peripheral oxygen saturation")}
SNOMED_BY_WORD = [("pneumonia", ("385093006", "Community acquired pneumonia")),
                  ("copd", ("13645005", "Chronic obstructive lung disease")),
                  ("oxygen", ("426990007", "Home oxygen therapy")),
                  ("immunosupp", ("38013005", "Immunosuppression")),
                  ("end-of-life", ("182964004", "Terminal care")), ("end of life", ("182964004", "Terminal care")),
                  ("sepsis", ("91302008", "Sepsis"))]


def snomed_for(c) -> tuple[str, str]:
    if c.kind == "structured" and c.field in SNOMED_BY_FIELD:
        return SNOMED_BY_FIELD[c.field]
    text = c.description.lower()
    for word, concept in SNOMED_BY_WORD:
        if word in text:
            return concept
    return ("", c.description)


def to_fhir(study: Study) -> dict:
    """HL7 FHIR R5 Bundle: ResearchStudy + definitional Group of eligibility characteristics."""
    group_id = f"{study.id}-eligibility"
    chars = []
    for c in study.criteria:
        code, display = snomed_for(c)
        coding = [{"system": "http://snomed.info/sct", "code": code, "display": display}] if code else []
        ch = {"code": {"coding": coding, "text": c.description}, "exclude": c.type.value == "exclusion"}
        if c.kind.value == "structured":
            op = c.operator.value
            q = {"value": c.value}
            ch["valueRange"] = {"low": q} if op in (">=", ">") else {"high": q} if op in ("<=", "<") else {"low": q, "high": q}
            if c.time_window_days:
                ch["durationDuration"] = {"value": c.time_window_days, "unit": "days", "system": "http://unitsofmeasure.org", "code": "d"}
        else:
            ch["valueBoolean"] = True
            ch["description"] = "Free-text criterion, checked in the clinical notes by two local models"
        chars.append(ch)
    status = "active" if study.status.value == "recruiting" else "closed"
    return {"resourceType": "Bundle", "type": "collection", "entry": [
        {"resource": {"resourceType": "ResearchStudy", "id": study.id, "title": study.title, "status": status,
                      "label": [{"value": study.short_title}] if study.short_title else [],
                      "condition": [{"text": study.condition}], "phase": {"text": study.phase or "n-a"},
                      "studyDesign": [{"text": study.study_design}],
                      "recruitment": {"targetNumber": study.target_sample_size, "eligibility": {"reference": f"Group/{group_id}"}},
                      "period": {"start": str(study.recruitment_start), "end": str(study.recruitment_end)},
                      "identifier": [{"system": "https://www.myresearchproject.org.uk", "value": study.ethics_reference}]}},
        {"resource": {"resourceType": "Group", "id": group_id, "type": "person", "membership": "definitional",
                      "characteristic": chars}},
    ]}


# ---------- What if: code recomputes each status without one criterion, reusing the cached verdicts ----------
# Extra candidates the SQL prefilter would let through if a structured criterion were removed (mock counts).
EXTRA_CANDIDATES = {"age": 3, "curb65": 41, "egfr": 6, "news2": 25, "crp": 18, "spo2": 12}


def _status_without(p: PatientView, study: Study, drop: str) -> FinalStatus:
    """Mirror of the decide.py rules, over the assessments that remain."""
    types = {c.id: c.type.value for c in study.criteria}
    remaining = [a for a in p.result.assessments if a.criterion_id != drop]
    if any(types.get(a.criterion_id) == "exclusion" and a.stage1 and a.stage2 and
           a.stage1.verdict == a.stage2.verdict == VerdictValue.met and not a.flags for a in remaining):
        return FinalStatus.excluded
    if any(a.flags for a in remaining):
        return FinalStatus.needs_review
    for a in remaining:
        verdicts = [v.verdict for v in (a.stage1, a.stage2) if v]
        if VerdictValue.insufficient in verdicts:
            return FinalStatus.needs_review
        if types.get(a.criterion_id) == "inclusion" and VerdictValue.not_met in verdicts:
            return FinalStatus.excluded
        if types.get(a.criterion_id) == "exclusion" and VerdictValue.met in verdicts:
            return FinalStatus.needs_review
    return FinalStatus.likely_eligible


def what_if(study: Study, patients: list[PatientView]) -> list[dict]:
    now = sum(p.result.status == FinalStatus.likely_eligible for p in patients)
    rows = []
    for c in study.criteria:
        statuses = [_status_without(p, study, c.id) for p in patients]
        rows.append({"criterion_id": c.id, "description": c.description, "type": c.type.value, "kind": c.kind.value,
                     "likely_eligible_now": now,
                     "likely_eligible_without": statuses.count(FinalStatus.likely_eligible),
                     "needs_review_without": statuses.count(FinalStatus.needs_review),
                     "extra_candidates_to_screen": EXTRA_CANDIDATES.get(c.field, 0) if c.kind.value == "structured" else 0})
    return rows


# ---------- Fairness: who is on the shortlist compared with everyone admitted (percentages) ----------
ADMITTED_BASELINE = {"Age": {"16 to 49": 22, "50 to 69": 31, "70 and over": 47},
                     "Sex": {"Female": 49, "Male": 51},
                     "Ethnic group": {"White": 78, "Asian or Asian British": 10, "Black or Black British": 5,
                                      "Mixed or other": 4, "Not stated": 3}}
MOCK_ETHNICITY = {"MRN100231": "White", "MRN100655": "White", "MRN100834": "White", "MRN100517": "Asian or Asian British",
                  "MRN100602": "White", "MRN100790": "Black or Black British"}


def _band(age: int) -> str:
    return "16 to 49" if age < 50 else "50 to 69" if age < 70 else "70 and over"


def fairness(patients: list[PatientView]) -> dict:
    shortlist = [p for p in patients if p.result.status != FinalStatus.excluded]
    n = len(shortlist) or 1
    groups = {"Age": [_band(p.age) for p in shortlist], "Sex": [p.sex for p in shortlist],
              "Ethnic group": [MOCK_ETHNICITY.get(p.patient_id, "Not stated") for p in shortlist]}
    out, flags = [], []
    for name, values in groups.items():
        rows = []
        for label, base in ADMITTED_BASELINE[name].items():
            pct = round(100 * values.count(label) / n)
            rows.append({"group": label, "admitted_pct": base, "shortlist_pct": pct})
            if base - pct >= 15:
                flags.append(f"{label} ({name.lower()}) is {base}% of admitted patients but {pct}% of the shortlist.")
        out.append({"characteristic": name, "rows": rows})
    return {"shortlist_size": len(shortlist), "groups": out, "flags": flags,
            "note": "Small numbers: a difference can be chance. Check whether a criterion is screening a group out."}


# ---------- Invitation letter: a draft for the research team to check, edit and send themselves ----------
def letter(study: Study, contact) -> str:
    # PatientContacts holds no name: the research team adds it before sending.
    to = f"Dear {contact.guardian_name}" if contact.guardian_name else "Dear [patient name]"
    return f"""{to},

You may be able to take part in a research study at the hospital{f" called {study.short_title}" if study.short_title else ""}. A member of the research team will tell you what it involves if you would like to hear more.

Your hospital care team found that you might be suitable by checking your hospital record. Your record has not been shared with anyone outside the hospital.

Taking part is your choice. If you decide not to, your care will not change in any way.

What happens next

If you would like to find out more, tell the nurse looking after you, or call the research team on 0114 496 0000. They will explain the study, answer your questions and check it is right for you.

If you do not want to hear about this study again, you do not need to do anything.

Yours sincerely,

{study.chief_investigator}
Chief investigator"""


# ---------- Screening log (ICH E6 good clinical practice): one row per patient screened ----------
STATUS_LABEL = {"likely_eligible": "Likely eligible", "needs_review": "Needs review", "excluded": "Excluded"}


def screening_log(patients: list[PatientView], not_shortlisted: int) -> list[dict]:
    rows = [{"patient_id": p.patient_id, "outcome": STATUS_LABEL[p.result.status.value],
             "reason": "; ".join(p.result.reasons) or p.summary, "decision": p.decision.value} for p in patients]
    rows.append({"patient_id": f"{not_shortlisted} other candidates", "outcome": "Not shortlisted at stage 1",
                 "reason": "An exclusion was clearly met with a cited record", "decision": ""})
    return rows


def screening_log_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["patient_id", "outcome", "reason", "decision"])
    w.writeheader(); w.writerows(rows)
    return buf.getvalue()
