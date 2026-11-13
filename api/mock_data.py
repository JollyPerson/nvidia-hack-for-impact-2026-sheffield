"""Mock responses with the same shapes as src/models.py, used until the real pipeline is wired in.

All data is synthetic. Contact details use Ofcom's drama range and example.com.
"""
import re
from datetime import date

from api.schemas import Contact, Funnel, PatientView, TraceStep
from src.models import (Citation, Criterion, CriterionAssessment, FinalStatus, Flag, FlagType, PatientResult, Study,
                        Verdict, VerdictValue)

C = Criterion
CAP_CRITERIA = [
    C(id="C1", type="inclusion", kind="structured", description="Aged 16 or over", field="age", operator=">=", value=16),
    C(id="C2", type="inclusion", kind="structured", description="CURB-65 score of 2 or more on admission",
      field="curb65", operator=">=", value=2),
    C(id="C3", type="inclusion", kind="notes",
      description="Clinical and radiological diagnosis of community-acquired pneumonia within the last 24 hours"),
    C(id="C4", type="exclusion", kind="structured", description="eGFR below 30 in the last 90 days",
      field="egfr", operator="<", value=30, time_window_days=90),
    C(id="C5", type="exclusion", kind="notes", description="COPD on long-term home oxygen"),
    C(id="C6", type="exclusion", kind="notes",
      description="Immunosuppressed (chemotherapy, high-dose steroids or organ transplant)"),
    C(id="C7", type="exclusion", kind="notes", description="Lacks capacity to consent or is receiving end-of-life care"),
]

STUDIES = [
    Study(id="S-001", title="Shorter antibiotic courses for adults admitted with community-acquired pneumonia",
          short_title="PRISM-CAP", condition="Community-acquired pneumonia", study_design="Randomised controlled trial",
          phase="Phase 4", intervention="3 days versus 7 days of oral amoxicillin after clinical stability",
          primary_outcome="Clinical cure at day 28", target_sample_size=60, recruitment_start=date(2026, 10, 1),
          recruitment_end=date(2027, 9, 30), chief_investigator="Dr Amira Shah", sponsor="Sheffield Teaching Research Trust (mock)",
          ethics_reference="IRAS 999001 (mock)", status="recruiting", criteria=CAP_CRITERIA),
    Study(id="S-002", title="Home monitoring after hospital admission for a COPD exacerbation", short_title="HOME-COPD",
          condition="Chronic obstructive pulmonary disease", study_design="Observational cohort", phase=None,
          intervention="Daily pulse oximetry and symptom diary for 30 days", primary_outcome="Readmission within 30 days",
          target_sample_size=120, recruitment_start=date(2026, 6, 1), recruitment_end=date(2027, 5, 31),
          chief_investigator="Dr Tom Okafor", sponsor="Northern Respiratory Network (mock)",
          ethics_reference="IRAS 999002 (mock)", status="recruiting",
          criteria=[C(id="C1", type="inclusion", kind="structured", description="Aged 40 or over", field="age", operator=">=", value=40),
                    C(id="C2", type="inclusion", kind="notes", description="Admitted with an exacerbation of COPD"),
                    C(id="C3", type="exclusion", kind="notes", description="No access to a phone at home")]),
    Study(id="S-003", title="Early warning scores and sepsis recognition on medical wards", short_title="NEWS-SEP",
          condition="Sepsis", study_design="Observational cohort", phase=None, intervention="None (observational)",
          primary_outcome="Time to antibiotics", target_sample_size=200, recruitment_start=date(2025, 1, 6),
          recruitment_end=date(2026, 1, 5), chief_investigator="Dr Lucy Barnes", sponsor="Mock University Hospitals",
          ethics_reference="IRAS 999003 (mock)", status="closed",
          criteria=[C(id="C1", type="inclusion", kind="structured", description="NEWS2 of 5 or more", field="news2", operator=">=", value=5)]),
]


def V(verdict, rid, quote, table="ClinicalNotes", field="NoteText", reasoning=""):
    return Verdict(verdict=verdict, reasoning=reasoning or _default_reason(verdict),
                   citations=[Citation(table=table, record_id=rid, field=field, quote=quote)])


def _default_reason(verdict):
    return {"met": "The record supports this criterion.", "not_met": "The record shows this criterion is not met.",
            "insufficient": "The records do not say enough to decide."}[verdict]


def sql_checks(mrn, age, curb, curb_rec, egfr, egfr_rec):
    """Structured criteria are checked by code (SQL and scores), not by a model. Shown as stage 1 with no stage 2."""
    code = "Checked by code from the structured record."
    return {"C1": (V("met", mrn, str(age), table="Patients", field="DateOfBirth (age)", reasoning=code), None),
            "C2": (V("met", curb_rec, str(curb), table="Observations", field="CURB-65 (calculated)", reasoning=code), None),
            "C4": (V("met" if egfr < 30 else "not_met", egfr_rec, str(egfr), table="Pathology", field="Value",
                     reasoning=code), None)}


DISAGREE = Flag(type=FlagType.stage_disagreement, detail="Stage 1 said met, stage 2 said not met.")


def _cap_patients():
    out = []

    def add(mrn, age, sex, status, summary, sql, notes, flags=None, reasons=()):
        flags = flags or {}
        assessments = [CriterionAssessment(criterion_id=cid, stage1=s1, stage2=s2, flags=flags.get(cid, []))
                       for cid, (s1, s2) in {**sql, **notes}.items()]
        assessments.sort(key=lambda a: int(a.criterion_id[1:]))
        out.append(PatientView(patient_id=mrn, age=age, sex=sex, summary=summary, result=PatientResult(
            study_id="S-001", patient_id=mrn, shortlisted=True, assessments=assessments, status=status,
            reasons=list(reasons))))

    E, R, X = FinalStatus.likely_eligible, FinalStatus.needs_review, FinalStatus.excluded
    add("MRN-100231", 72, "Female", E,
        "CAP confirmed on chest X-ray, CURB-65 of 2. No COPD, not immunosuppressed, has capacity.",
        sql_checks("MRN-100231", 72, 2, "OB-77120", 58, "PA-55310"),
        {"C3": (V("met", "N-1042", "CXR: R lower lobe consolidation c/w CAP"), V("met", "N-1042", "R lower lobe consolidation c/w CAP")),
         "C5": (V("not_met", "N-1042", "No hx of COPD, not on home O2"), V("not_met", "N-1042", "No hx of COPD, not on home O2")),
         "C6": (V("not_met", "N-1043", "No immunosuppressants. No recent chemo"), V("not_met", "N-1043", "No immunosuppressants")),
         "C7": (V("not_met", "N-1044", "Alert and orientated, has capacity to make decisions about treatment"),
                V("not_met", "N-1044", "has capacity to make decisions about treatment"))})
    add("MRN-100655", 77, "Female", E,
        "Left basal pneumonia on X-ray, CURB-65 of 3. No exclusions found by either model.",
        sql_checks("MRN-100655", 77, 3, "OB-77391", 47, "PA-55621"),
        {"C3": (V("met", "N-1201", "CXR L basal consolidation. Dx CAP, IV co-amox started"), V("met", "N-1201", "Dx CAP")),
         "C5": (V("not_met", "N-1201", "Asthma as a child, no COPD"), V("not_met", "N-1201", "no COPD")),
         "C6": (V("not_met", "N-1202", "Not on steroids or immunosuppressants"), V("not_met", "N-1202", "Not on steroids or immunosuppressants")),
         "C7": (V("not_met", "N-1203", "Capacity assessed: has capacity"), V("not_met", "N-1203", "has capacity"))})
    add("MRN-100834", 70, "Female", E,
        "Right middle lobe pneumonia, CURB-65 of 2. No exclusions.",
        sql_checks("MRN-100834", 70, 2, "OB-77540", 66, "PA-55790"),
        {"C3": (V("met", "N-1330", "RML consolidation, clinical picture of CAP"), V("met", "N-1330", "clinical picture of CAP")),
         "C5": (V("not_met", "N-1330", "Ex-smoker, spirometry normal 2024, no COPD"), V("not_met", "N-1330", "no COPD")),
         "C6": (V("not_met", "N-1331", "no immunosuppression"), V("not_met", "N-1331", "no immunosuppression")),
         "C7": (V("not_met", "N-1332", "Lives alone, independent, capacity not in doubt"), V("not_met", "N-1332", "capacity not in doubt"))})
    add("MRN-100517", 67, "Female", R,
        "The models disagree on COPD. The note mentions COPD and home oxygen, but about the patient's mother.",
        sql_checks("MRN-100517", 67, 2, "OB-77202", 71, "PA-55402"),
        {"C3": (V("met", "N-1088", "Bilateral lower zone consolidation, treating as CAP"), V("met", "N-1088", "treating as CAP")),
         "C5": (V("met", "N-1089", "COPD on home oxygen", reasoning="The note mentions COPD on home oxygen."),
                V("not_met", "N-1089", "Mother had COPD on home oxygen. Pt never smoked, no resp hx",
                  reasoning="COPD and home oxygen refer to the patient's mother, not the patient.")),
         "C6": (V("not_met", "N-1089", "no immunosuppressants"), V("not_met", "N-1089", "no immunosuppressants")),
         "C7": (V("not_met", "N-1090", "has capacity"), V("not_met", "N-1090", "has capacity"))},
        flags={"C5": [DISAGREE.model_copy(update={"detail": "Stage 1 said met, stage 2 said not met. Check who the note is about."})]})
    add("MRN-100602", 59, "Male", R,
        "A quote from stage 1 was not found in the cited note, so the steroid exclusion needs checking.",
        sql_checks("MRN-100602", 59, 2, "OB-77260", 81, "PA-55470"),
        {"C3": (V("met", "N-1110", "Consolidation L upper lobe, CAP"), V("met", "N-1110", "CAP")),
         "C5": (V("not_met", "N-1110", "No COPD"), V("not_met", "N-1110", "No COPD")),
         "C6": (V("met", "N-1111", "on prednisolone 40mg daily"),
                V("not_met", "N-1111", "Prednisolone course for gout completed in August")),
         "C7": (V("not_met", "N-1112", "has capacity"), V("not_met", "N-1112", "has capacity"))},
        flags={"C6": [Flag(type=FlagType.citation_failed, stage=1,
                           detail="The quote 'on prednisolone 40mg daily' does not appear in note N-1111."), DISAGREE]})
    add("MRN-100790", 64, "Male", R,
        "Confused on admission and capacity has not been assessed yet.",
        sql_checks("MRN-100790", 64, 3, "OB-77488", 62, "PA-55730"),
        {"C3": (V("met", "N-1290", "CXR R basal consolidation, CAP"), V("met", "N-1290", "CAP")),
         "C5": (V("not_met", "N-1290", "no resp hx"), V("not_met", "N-1290", "no resp hx")),
         "C6": (V("not_met", "N-1291", "no immunosuppression"), V("not_met", "N-1291", "no immunosuppression")),
         "C7": (V("insufficient", "N-1292", "Confused on admission, ?delirium. Capacity not yet assessed"),
                V("insufficient", "N-1292", "Capacity not yet assessed"))})
    add("MRN-100488", 81, "Male", X,
        "COPD on long-term home oxygen, confirmed by both models.",
        sql_checks("MRN-100488", 81, 3, "OB-77180", 44, "PA-55360"),
        {"C3": (V("met", "N-1061", "LLL consolidation, CAP"), V("met", "N-1061", "LLL consolidation, CAP")),
         "C5": (V("met", "N-1062", "Known COPD, on home O2 2L via concentrator 16h/day"),
                V("met", "N-1062", "on home O2 2L via concentrator 16h/day"))},
        reasons=["C5: COPD on long-term home oxygen"])
    add("MRN-100713", 88, "Male", X,
        "Receiving end-of-life care, confirmed by both models.",
        sql_checks("MRN-100713", 88, 4, "OB-77420", 35, "PA-55660"),
        {"C3": (V("met", "N-1250", "Aspiration vs CAP, R base"), V("met", "N-1250", "Aspiration vs CAP, R base")),
         "C7": (V("met", "N-1251", "On end of life care pathway, family aware"), V("met", "N-1251", "On end of life care pathway"))},
        reasons=["C7: receiving end-of-life care"])
    return out


CONTACTS = {
    "MRN-100231": Contact(patient_id="MRN-100231", name="Margaret Hill", mobile="07700 900231", email="m.hill@example.com"),
    "MRN-100655": Contact(patient_id="MRN-100655", name="Joan Patel", mobile="07700 900655", email="j.patel@example.com"),
    "MRN-100834": Contact(patient_id="MRN-100834", name="Susan Clarke", mobile="07700 900834", email="s.clarke@example.com"),
    "MRN-100517": Contact(patient_id="MRN-100517", name="Priya Singh", mobile="07700 900517", email="p.singh@example.com"),
    "MRN-100602": Contact(patient_id="MRN-100602", name="David Owen", mobile="07700 900602", email="d.owen@example.com"),
    "MRN-100790": Contact(patient_id="MRN-100790", name="Robert King", mobile="07700 900790", email="r.king@example.com"),
}


def _generic_patients(study: Study):
    """Same patients for a study created in the UI: each criterion reuses a PRISM-CAP verdict pattern."""
    cap = {c.id: c for c in CAP_CRITERIA}
    out = []
    for p in _cap_patients():
        by_id = {a.criterion_id: a for a in p.result.assessments}
        notes_src = [a for cid, a in by_id.items() if cap[cid].kind == "notes"]
        new, n = [], 0
        for c in study.criteria:
            if c.kind == "structured":
                src = by_id["C1"]
            else:
                src = notes_src[n % len(notes_src)]; n += 1
            a = src.model_copy(deep=True, update={"criterion_id": c.id})
            if c.type == "inclusion":
                for v in (a.stage1, a.stage2):
                    if v is not None and v.verdict == "not_met":
                        v.verdict = VerdictValue.met
            new.append(a)
        result = p.result.model_copy(update={"study_id": study.id, "assessments": new,
                                             "reasons": [r.split(":", 1)[-1].strip() for r in p.result.reasons]})
        out.append(p.model_copy(update={"result": result}))
    return out


def patients_for(study: Study):
    return _cap_patients() if study.id == "S-001" else _generic_patients(study)


def funnel_for(patients):
    statuses = [p.result.status for p in patients]
    from api.extras import DO_NOT_CONTACT
    return Funnel(removed_before_screening=[{"reason": r, "count": n} for r, n in DO_NOT_CONTACT],
                  candidates_after_sql=len(patients) + 14, shortlisted_stage1=sum(p.result.shortlisted for p in patients),
                  confirmed_stage2=sum(s != FinalStatus.excluded for s in statuses),
                  likely_eligible=statuses.count(FinalStatus.likely_eligible),
                  needs_review=statuses.count(FinalStatus.needs_review),
                  excluded=statuses.count(FinalStatus.excluded) + 14)


def trace_for(study: Study, funnel: Funnel):
    n_notes = sum(c.kind == "notes" for c in study.criteria)
    n_struct = len(study.criteria) - n_notes
    return [
        TraceStep(step=1, name="Prefilter: SQL on the warehouse",
                  detail=f"Built a parameterised query from {n_struct} structured criteria. "
                         f"{funnel.candidates_after_sql} of 412 admitted patients are candidates. "
                         f"{sum(g.count for g in funnel.removed_before_screening)} patients with a do-not-contact flag "
                         "were removed first.", seconds=0.04),
        TraceStep(step=2, name="Stage 1: screener reads notes", model="SCREENER",
                  detail=f"Checked {n_notes} notes criteria for each candidate, one call per criterion. "
                         f"Shortlisted {funnel.shortlisted_stage1} patients.", seconds=14.2),
        TraceStep(step=3, name="Stage 2: verifier re-checks original records", model="VERIFIER",
                  detail=f"Independently re-read the records of the {funnel.shortlisted_stage1} shortlisted patients.",
                  seconds=9.8),
        TraceStep(step=4, name="Validation: check every citation", detail="Checked every quote against the cited record. "
                  "1 quote was not found, 2 disagreements flagged. Nothing was removed.", seconds=0.2),
        TraceStep(step=5, name="Decide: code combines verdicts and flags",
                  detail=f"{funnel.likely_eligible} likely eligible, {funnel.needs_review} need review, "
                         f"{funnel.excluded} excluded.", seconds=0.01),
    ]


# ---------- Free-text helper for the create form (mock of the LLM criteria drafter) ----------
EXAMPLE_DESCRIPTION = (
    "Adults aged 16 or over admitted with community-acquired pneumonia in the last 24 hours, with a CURB-65 score "
    "of 2 or more. Leave out anyone with an eGFR below 30 in the last 90 days, anyone with COPD on home oxygen, "
    "anyone who is immunosuppressed, and anyone who lacks capacity or is receiving end-of-life care."
)


def draft_criteria(text: str):
    crits, assumptions = [], []
    t = text.strip()
    m = re.search(r"aged (\d+) or over|(\d+) and over|over (\d+)", t, re.I)
    if m:
        age = int(next(g for g in m.groups() if g))
        crits.append(C(type="inclusion", kind="structured", description=f"Aged {age} or over", field="age", operator=">=", value=age))
    elif re.search(r"\badults?\b", t, re.I):
        crits.append(C(type="inclusion", kind="structured", description="Aged 18 or over", field="age", operator=">=", value=18))
        assumptions.append("Taken 'adults' to mean aged 18 or over.")
    m = re.search(r"CURB-?65[^.,]*?(\d)", t, re.I)
    if m:
        crits.append(C(type="inclusion", kind="structured", description=f"CURB-65 score of {m.group(1)} or more",
                       field="curb65", operator=">=", value=int(m.group(1))))
    parts = re.split(r"(?i)\b(?:leave out|exclude|excluding|but not|apart from)\b", t, maxsplit=1)
    incl_text, excl_text = parts[0], (parts[1] if len(parts) > 1 else "")
    cond = re.search(r"(?:with|admitted with|diagnosed with) ([^,.]+)", incl_text, re.I)
    if cond:
        crits.append(C(type="inclusion", kind="notes", description=cond.group(1).strip().capitalize()))
    for piece in re.split(r",| and anyone| or anyone", excl_text):
        piece = re.sub(r"(?i)^\s*(and\s+)?(anyone|people|patients)\s+", "", piece)
        piece = re.sub(r"(?i)^(who\s+)?(is|are|has|have|with)?\s*", "", piece.strip()).strip(" .")
        if len(piece) < 4:
            continue
        e = re.search(r"(?i)egfr (?:below|under|less than) (\d+)(?: in the last (\d+) days)?", piece)
        if e:
            crits.append(C(type="exclusion", kind="structured", description=f"eGFR below {e.group(1)}"
                           + (f" in the last {e.group(2)} days" if e.group(2) else ""), field="egfr", operator="<",
                           value=int(e.group(1)), time_window_days=int(e.group(2)) if e.group(2) else None))
        else:
            crits.append(C(type="exclusion", kind="notes", description=piece[0].upper() + piece[1:]))
    if any("immunosuppressed" in c.description.lower() for c in crits):
        assumptions.append("Taken 'immunosuppressed' to include chemotherapy, high-dose steroids and organ transplant.")
    if re.search(r"last 24 hours", t, re.I):
        assumptions.append("'In the last 24 hours' is counted from the time of admission.")
    for i, c in enumerate(crits, 1):
        c.id = f"C{i}"
    return crits, assumptions
