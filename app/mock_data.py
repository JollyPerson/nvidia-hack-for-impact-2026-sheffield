"""Mock data for the TrialMatch UI mockup.

Everything here is invented and synthetic. The shapes follow the agent contracts in
PLAN.md so the real pipeline can replace this module function by function:
  get_trials()            -> list of trials (cached ClinicalTrials.gov JSON, simplified)
  get_criteria(trial_id)  -> criteria agent output
  get_run(trial_id)       -> {trace, funnel, stats, results}
"""

TRIALS = [
    {
        "id": "NCT-MOCK-0001",
        "title": "Neoadjuvant immunotherapy in early-stage non-small cell lung cancer (Phase II)",
        "sponsor": "Mock Oncology Network",
        "phase": "Phase II",
        "target": 40,
        "eligibility_text": (
            "Inclusion: Age 18 or over. BMI 18.5 to 35. Histologically confirmed NSCLC stage I-IIIA. "
            "ECOG performance status 0-1. Seen in primary care within the last 12 months.\n"
            "Exclusion: Autoimmune disease requiring systemic treatment in the past 2 years. "
            "Prior chemotherapy or radiotherapy for lung cancer. Pregnant or planning pregnancy."
        ),
    },
    {
        "id": "NCT-MOCK-0002",
        "title": "Exercise prehabilitation before colorectal cancer surgery",
        "sponsor": "Mock University Hospitals",
        "phase": "N/A (behavioural)",
        "target": 120,
        "eligibility_text": "Inclusion: Age 50-85. Planned elective colorectal resection...",
    },
    {
        "id": "NCT-MOCK-0003",
        "title": "Low-dose aspirin for prevention of breast cancer recurrence",
        "sponsor": "Mock Cancer Trials Unit",
        "phase": "Phase III",
        "target": 300,
        "eligibility_text": "Inclusion: Female, age 18-70. Completed adjuvant therapy for HER2-negative breast cancer...",
    },
]

CRITERIA = {
    "NCT-MOCK-0001": [
        {"id": "C1", "text": "Age 18 or over", "type": "inclusion", "kind": "structured", "field": "age", "op": ">=", "value": 18},
        {"id": "C2", "text": "BMI between 18.5 and 35", "type": "inclusion", "kind": "structured", "field": "bmi", "op": "between", "value": [18.5, 35]},
        {"id": "C3", "text": "Histologically confirmed NSCLC, stage I-IIIA", "type": "inclusion", "kind": "free_text"},
        {"id": "C4", "text": "ECOG performance status 0-1", "type": "inclusion", "kind": "free_text"},
        {"id": "C5", "text": "Seen in primary care within the last 12 months", "type": "inclusion", "kind": "free_text"},
        {"id": "C6", "text": "Autoimmune disease requiring systemic treatment in the past 2 years", "type": "exclusion", "kind": "free_text"},
        {"id": "C7", "text": "Prior chemotherapy or radiotherapy for lung cancer", "type": "exclusion", "kind": "free_text"},
        {"id": "C8", "text": "Pregnant or planning pregnancy", "type": "exclusion", "kind": "free_text"},
    ],
}

TRACE = [
    {"step": 1, "agent": "Criteria parser (LLM 2)", "tool": "llm2.parse_criteria", "model": "gpt-oss-120b", "ms": 3820,
     "tokens": "1 call, 52 tokens a second output",
     "detail": "Found 8 criteria in the eligibility text. 2 can be checked against the patient record. 6 need the GP notes to be read."},
    {"step": 2, "agent": "SQL query on the data warehouse", "tool": "duckdb (read only)", "model": None, "ms": 41,
     "tokens": "No AI model used",
     "detail": "Age under 18 ruled out 41 patients. BMI outside 18.5 to 35 ruled out 22 more. 137 patients and their 684 notes passed to the next step."},
    {"step": 3, "agent": "Notes screening (LLM 1)", "tool": "llm1.screen (32 at a time)", "model": "Nemotron Nano 9B v2", "ms": 18900,
     "tokens": "41,200 tokens a second input (prefill)",
     "detail": "Read every note for 137 patients. Made 822 decisions: 214 met, 497 not met, 111 not known. Ruled out 96 patients with an exclusion. 41 remain."},
    {"step": 4, "agent": "Shortlist review (LLM 2)", "tool": "llm2.finalise", "model": "gpt-oss-120b", "ms": 4630,
     "tokens": "48 tokens a second output (decode)",
     "detail": "Reviewed the 41 remaining patients. Ranked 12 as likely eligible and wrote the reason and quotes for each."},
    {"step": 5, "agent": "Validation", "tool": "validate.quotes_and_records", "model": None, "ms": 210,
     "tokens": "No AI model used",
     "detail": "Checked 1,233 quotes against the original notes and every record value against the warehouse. 3 quotes were not found word for word, so those answers were changed to not known. 0 unchecked quotes reached you."},
]

SQL = """SELECT p.patient_id, p.age, p.sex, p.bmi
FROM patients AS p
WHERE p.age >= ?            -- C1: 18
  AND p.bmi BETWEEN ? AND ? -- C2: 18.5, 35
  AND p.practice_id = ?     -- demo practice
ORDER BY p.patient_id;"""

SQL_FUNNEL = [("All patients at the practice", 200), ("C1: age 18 or over", 159), ("C2: BMI 18.5 to 35", 137)]

FUNNEL = [
    ("GP practice list", 200),
    ("Passed SQL record checks", 137),
    ("No exclusion found in notes", 41),
    ("Likely eligible (shortlist)", 12),
]

STATS = {
    "model": "gpt-oss-120b + Nemotron Nano 9B v2",
    "host": "DGX Spark (GB10, 128 GB)",
    "endpoint": "http://localhost:8000/v1",
    "bytes_off_device": 0,
    "notes_per_min": 2170,
    "gpu_mem_gb": 96.2,
    "runtime_s": 27.6,
}

# ---------- Create a trial from free text ----------
CREATE_EXAMPLE = (
    "I need adults with early non-small cell lung cancer (stage 1 to 3A) who are still fully active or nearly so, "
    "and who have seen their GP in the last year. BMI between 18.5 and 35. Please leave out anyone who has had chemo "
    "or radiotherapy for their lung cancer, anyone treated for an autoimmune condition in the last 2 years, "
    "and anyone who is pregnant or planning a pregnancy."
)

# Criteria agent output for CREATE_EXAMPLE. "from" is the phrase in the description each criterion came from.
_PARSED_FROM = {
    "C1": ("adults", "Taken 'adults' to mean age 18 or over."),
    "C2": ("BMI between 18.5 and 35", None),
    "C3": ("early non-small cell lung cancer (stage 1 to 3A)", None),
    "C4": ("still fully active or nearly so", "Taken 'fully active or nearly so' to mean ECOG performance status 0 or 1."),
    "C5": ("seen their GP in the last year", "Taken 'the last year' to mean the 12 months before today."),
    "C6": ("treated for an autoimmune condition in the last 2 years", "Taken 'treated' to mean systemic treatment, such as steroids or immunosuppressants. Creams and thyroid replacement alone do not count."),
    "C7": ("had chemo or radiotherapy for their lung cancer", None),
    "C8": ("pregnant or planning a pregnancy", None),
}


def parse_description(text):
    """Mock of the criteria agent on free text. Real version: LLM 2 with guided JSON output."""
    out = []
    for c in CRITERIA["NCT-MOCK-0001"]:
        phrase, assumption = _PARSED_FROM[c["id"]]
        out.append({**c, "from": phrase, "assumption": assumption})
    return out


# Status per criterion: MET / NOT_MET / UNKNOWN. Structured criteria cite the record field, free text cites a note.
def _s(field, value):
    return {"status": "MET", "source": f"Patient record: {field.upper() if field == 'bmi' else field} {value}", "quote": None, "note": None, "confidence": 1.0}

def _n(status, quote, note_id, date, note, conf):
    return {"status": status, "source": f"GP note {note_id}, {date}", "quote": quote, "note": note, "confidence": conf}

RESULTS = [
    {
        "patient_id": "P-0142", "name": "Margaret H.", "age": 67, "sex": "F", "bmi": 26.1, "score": 0.96,
        "explanation": "Stage IB adenocarcinoma, ECOG 0, seen 3 weeks ago, no autoimmune or prior treatment history.",
        "criteria": {
            "C1": _s("age", 67), "C2": _s("bmi", 26.1),
            "C3": _n("MET", "Biopsy confirms adenocarcinoma of R upper lobe, staged T2aN0M0 (stage IB)", "N-0142-04", "2 October 2026",
                     "Results review. Biopsy confirms adenocarcinoma of R upper lobe, staged T2aN0M0 (stage IB). Discussed MDT outcome, surgical referral made. Pt understands, daughter present.", 0.94),
            "C4": _n("MET", "fully active, walks dog 2 miles daily, ECOG 0", "N-0142-05", "21 October 2026",
                     "F/U post MDT. Feels well in herself. Remains fully active, walks dog 2 miles daily, ECOG 0. Mild cough, no haemoptysis. Wt stable.", 0.91),
            "C5": _n("MET", "F/U post MDT", "N-0142-05", "21 October 2026",
                     "F/U post MDT. Feels well in herself. Remains fully active, walks dog 2 miles daily, ECOG 0. Mild cough, no haemoptysis. Wt stable.", 0.99),
            "C6": _n("NOT_MET", "No hx of thyroid or other autoimmune disease", "N-0142-01", "14 March 2025",
                     "New pt check. Ex-smoker 30 pack-yrs, quit 2019. HTN on amlodipine. No hx of thyroid or other autoimmune disease. FHx: father lung ca.", 0.88),
            "C7": _n("NOT_MET", "treatment naive, surgical referral made", "N-0142-04", "2 October 2026",
                     "Results review. Biopsy confirms adenocarcinoma, stage IB. Pt treatment naive, surgical referral made.", 0.85),
            "C8": _n("NOT_MET", "post-menopausal since 2008", "N-0142-01", "14 March 2025",
                     "New pt check. Post-menopausal since 2008. Ex-smoker 30 pack-yrs, quit 2019.", 0.97),
        },
    },
    {
        "patient_id": "P-0077", "name": "David O.", "age": 59, "sex": "M", "bmi": 29.4, "score": 0.88,
        "explanation": "Stage IIA squamous NSCLC, ECOG 1, seen last month. Autoimmune history not mentioned anywhere.",
        "criteria": {
            "C1": _s("age", 59), "C2": _s("bmi", 29.4),
            "C3": _n("MET", "squamous cell ca L lower lobe, stage IIA", "N-0077-03", "18 September 2026",
                     "Letter from resp team: squamous cell ca L lower lobe, stage IIA. Awaiting oncology clinic. Pt anxious, offered Macmillan contact.", 0.92),
            "C4": _n("MET", "SOB on hills only, independent with ADLs, still working part time", "N-0077-04", "8 October 2026",
                     "Review. SOB on hills only, independent with ADLs, still working part time. Sats 95% RA.", 0.74),
            "C5": _n("MET", "Review.", "N-0077-04", "8 October 2026", "Review. SOB on hills only, independent with ADLs.", 0.99),
            "C6": _n("UNKNOWN", None, None, None, None, 0.41),
            "C7": _n("NOT_MET", "no prior oncological treatment", "N-0077-03", "18 September 2026",
                     "Letter from resp team: squamous cell ca, stage IIA. No prior oncological treatment. Awaiting oncology clinic.", 0.83),
            "C8": _n("NOT_MET", "Not applicable (male)", "Record", "-", None, 1.0),
        },
    },
    {
        "patient_id": "P-0019", "name": "Priya S.", "age": 52, "sex": "F", "bmi": 22.8, "score": 0.81,
        "explanation": "Stage IIIA adenocarcinoma, ECOG 1. Pregnancy plans are not discussed, check before contacting.",
        "criteria": {
            "C1": _s("age", 52), "C2": _s("bmi", 22.8),
            "C3": _n("MET", "EGFR-neg adenocarcinoma, stage IIIA per oncology letter", "N-0019-02", "30 August 2026",
                     "Onc letter scanned. EGFR-neg adenocarcinoma, stage IIIA per oncology letter. Plan for neoadjuvant discussion.", 0.9),
            "C4": _n("MET", "tires by afternoon but self-caring, ECOG 1", "N-0019-03", "12 September 2026",
                     "Telephone consult. Tires by afternoon but self-caring, ECOG 1. Appetite ok.", 0.89),
            "C5": _n("MET", "Telephone consult.", "N-0019-03", "12 September 2026", "Telephone consult. Tires by afternoon but self-caring.", 0.98),
            "C6": _n("NOT_MET", "no autoimmune hx", "N-0019-01", "5 January 2026", "Annual review. Asthma, well controlled on salbutamol prn. No autoimmune hx.", 0.86),
            "C7": _n("NOT_MET", "Plan for neoadjuvant discussion", "N-0019-02", "30 August 2026", "Onc letter scanned. Plan for neoadjuvant discussion.", 0.7),
            "C8": _n("UNKNOWN", None, None, None, None, 0.38),
        },
    },
    {
        "patient_id": "P-0163", "name": "Alan W.", "age": 71, "sex": "M", "bmi": 24.0, "score": 0.0,
        "explanation": "Excluded: on levothyroxine and prednisolone for autoimmune thyroiditis flare (2025).",
        "excluded": True,
        "criteria": {
            "C1": _s("age", 71), "C2": _s("bmi", 24.0),
            "C3": _n("MET", "stage IB NSCLC confirmed at MDT", "N-0163-05", "11 October 2026", "Stage IB NSCLC confirmed at MDT. For lobectomy.", 0.93),
            "C4": _n("MET", "ECOG 0", "N-0163-05", "11 October 2026", "Stage IB NSCLC confirmed at MDT. ECOG 0. For lobectomy.", 0.95),
            "C5": _n("MET", "For lobectomy.", "N-0163-05", "11 October 2026", "Stage IB NSCLC confirmed at MDT. For lobectomy.", 0.99),
            "C6": _n("MET", "Hashimoto's thyroiditis flare, started pred 30mg reducing course", "N-0163-02", "19 June 2025",
                     "Seen with fatigue, goitre. TFTs deranged. Hashimoto's thyroiditis flare, started pred 30mg reducing course alongside levothyroxine.", 0.93),
            "C7": _n("NOT_MET", "treatment naive", "N-0163-05", "11 October 2026", "Stage IB NSCLC confirmed at MDT. Treatment naive.", 0.8),
            "C8": _n("NOT_MET", "Not applicable (male)", "Record", "-", None, 1.0),
        },
    },
]

# Fill out the shortlist with lighter rows so the table looks realistic.
_EXTRA = [
    ("P-0088", "Susan T.", 63, "F", 31.2, 0.77, "Stage IIB, ECOG 1. Last GP contact 10 months ago."),
    ("P-0021", "Robert K.", 74, "M", 27.5, 0.74, "Stage IA, ECOG 0. Prior radiotherapy is unclear (prostate, not lung)."),
    ("P-0130", "Fatima A.", 48, "F", 20.9, 0.69, "Stage IIIA. Performance status not documented, recruiter to confirm."),
    ("P-0055", "John M.", 68, "M", 33.8, 0.66, "Stage IIA. BMI near the upper limit."),
]
for pid, name, age, sex, bmi, score, expl in _EXTRA:
    RESULTS.append({"patient_id": pid, "name": name, "age": age, "sex": sex, "bmi": bmi, "score": score, "explanation": expl,
                    "criteria": {c: {"status": s, "source": "", "quote": None, "note": None, "confidence": 0.8}
                                 for c, s in zip(["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
                                                 ["MET", "MET", "MET", "UNKNOWN" if score < 0.7 else "MET", "MET", "NOT_MET", "UNKNOWN" if score < 0.75 else "NOT_MET", "NOT_MET"])}})


def get_trials():
    return TRIALS


def get_criteria(trial_id):
    return CRITERIA.get(trial_id, CRITERIA["NCT-MOCK-0001"])


def get_run(trial_id):
    return {"trace": TRACE, "funnel": FUNNEL, "stats": STATS, "results": RESULTS, "sql": SQL, "sql_funnel": SQL_FUNNEL}


# ---------- Standards ----------
# SNOMED CT concepts for each criterion. Illustrative: check codes against the UK SNOMED CT browser before real use.
SNOMED = {
    "C1": ("424144002", "Current chronological age"),
    "C2": ("60621009", "Body mass index"),
    "C3": ("254637007", "Non-small cell lung cancer"),
    "C4": ("423740007", "ECOG performance status"),
    "C5": ("11429006", "Consultation"),
    "C6": ("85828009", "Autoimmune disease"),
    "C7": ("367336001", "Chemotherapy"),
    "C8": ("77386006", "Pregnancy"),
}

# Mock "what if" model: how the shortlist size changes if a criterion is relaxed or dropped.
WHATIF_BASE = 12


def whatif(bmi_max=35, age_min=18, dropped=()):
    """Real version: re-run SQL counts and reuse cached LLM 1 verdicts (no new LLM calls needed)."""
    n = WHATIF_BASE + round(max(0, bmi_max - 35) * 1.8) - round(max(0, 35 - bmi_max) * 1.5)
    n += {16: 0, 18: 0}.get(age_min, -round((age_min - 18) * 0.05))
    n += sum({"C4": 4, "C5": 3, "C6": 5, "C7": 2, "C8": 1}.get(c, 0) for c in dropped)
    return max(n, 0)
