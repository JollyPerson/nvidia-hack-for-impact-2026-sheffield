# AGENTS.md — Project Advolare

Read this file in full before doing any work. Follow it over your own preferences.

## What this project is

Advolare is a clinical trial recruitment tool for NHS hospitals. A clinician creates a study with eligibility criteria; the system searches a hospital data warehouse, uses two local open-weight LLMs to reason through patients' free-text clinical notes against the criteria, verifies every piece of evidence the models cite, and gives the clinician a reviewable list of likely-eligible patients.

Everything runs on a single NVIDIA DGX Spark. No patient data is sent to any external service. All data in this repository is fully synthetic.

## Pipeline

```
Clinician creates study (web)
  → prefilter: code builds parameterised SQL from structured criteria
  → warehouse returns candidate patients (SQLite)
  → stage 1 (screener LLM): reads each candidate's records and notes, shortlists, cites evidence
  → stage 2 (verifier LLM): independently re-checks each shortlisted patient from the ORIGINAL records, cites evidence
  → validation layer: code checks every citation exists and matches the record
  → decide: code combines verdicts + validation flags → eligible / excluded / needs review
  → clinician reviews results (web)
```

**Code versus LLM — never blur this:**
- Code: SQL generation, clinical scores, citation validation, final eligibility decision.
- LLM: reading free-text notes and judging criteria that structured data cannot answer.
- The LLM never writes SQL and never makes the final eligibility decision.

## Stack

- Python 3.11+
- Backend: FastAPI + Uvicorn
- Frontend: plain HTML, CSS, minimal vanilla JavaScript, served by FastAPI. No frameworks, no build step.
- Database: SQLite (`data/warehouse.db`)
- Models: served locally through an OpenAI-compatible API (vLLM or Ollama). All model calls go through `src/llm.py` only.
- Validation: Pydantic for every model output
- Data generation: Faker + rule-based logic + LLM-generated notes
- Tests: pytest

Do not add any other framework or dependency without asking.

## Directory

```
advolare/
  AGENTS.md            Rules every coding agent reads first. Owns: project summary, stack, rules, run commands.
  config.py            One place for settings. Owns: model per role, server URL, DB path, step limit, temperature, seed.
  requirements.txt     Reproducible installs. Owns: package list.

  src/
    models.py          Shared contracts so modules can't drift. Owns: Study, Criterion, Citation, Verdict, PatientResult.
    pipeline.py        Readable step-by-step flow. Owns: stage order only, no logic.
    scores.py          Clinical scores must be exact, not guessed. Owns: NEWS2, CURB-65 calculations.
    prefilter.py       Cheap, reliable first cut. Owns: structured criteria → parameterised SQL → candidate list.
    llm.py             Single gateway to the models. Owns: model-server client, role → model mapping, JSON output enforcement.
    tools.py           Let the agent fetch evidence itself. Owns: search_notes, get_labs, get_observations, get_diagnoses + schemas.
    screening.py       The agentic funnel. Owns: tool loop, stage 1 (shortlist), stage 2 (verification).
    validation.py      Catch invented evidence. Owns: checking every citation against the warehouse; flags, never removes.
    decide.py          Final call made by code. Owns: verdicts + flags → eligible / excluded / needs review.
    audit.py           Traceability. Owns: logging every prompt, output, flag and decision.

  api/
    main.py            Connect the pipeline to the browser. Owns: all HTTP endpoints.

  web/
    index.html         Browse studies.
    study.html         View one study: details, criteria, results.
    new_study.html     Create a study.
    static/            NHS.UK frontend CSS, small JS.

  data/
    schema.sql         Warehouse tables.
    generate.py        Builds warehouse.db from scratch (seeded, reproducible).
    hero_patients.py   Hand-built test patients with planted traps and known answers.
    warehouse.db       Generated. Not committed.

  eval/
    labels.json        Correct verdict per test patient per criterion.
    evaluate.py        Recall, precision, per-criterion and hard-case accuracy, per-stage results.

  scripts/
    run_cli.py         Run a screening from the terminal.

  tests/               Tests for scores, prefilter, validation, decide.
```

## Frontend

Two functions only. Build nothing beyond them.

**1. Browse studies**
- List of studies (title, condition, status, target sample size).
- Clicking a study shows its full details, criteria, and screening results.

**2. Create a study**
- A form capturing the fields below, then saves the study.
- A separate "Run screening" button on the study page starts the pipeline. Creating a study does not run it automatically.

**Study fields** (modelled on ISRCTN / HRA study summaries):
- Title, short title / acronym
- Condition studied
- Study design (e.g. randomised controlled trial, observational)
- Phase (if applicable)
- Intervention
- Primary outcome
- Target sample size
- Recruitment start and end dates
- Chief investigator, sponsor
- Ethics reference (REC / IRAS)
- Inclusion criteria (list)
- Exclusion criteria (list)
- Status (recruiting, closed)

Each criterion is entered as a row with: type (inclusion / exclusion), free-text description, and kind:
- **structured** — checkable from a field (e.g. age ≥ 16, CURB-65 ≥ 2, eGFR < 30 in last 90 days): select field, operator, value, optional time window
- **notes** — needs free-text reasoning (e.g. "COPD on home oxygen")

**Results view** (on the study page):
- Funnel counts: candidates after SQL → shortlisted by stage 1 → confirmed by stage 2 → final status counts.
- Three lists: Likely eligible, Needs review, Excluded.
- Clicking a patient shows each criterion's verdict from both models, the cited evidence, and any validation flags.
- Contact details are shown only after the clinician clicks "Approve" on that patient.

**Look and feel:**
- Use the NHS.UK frontend design system (open-source CSS and components) and NHS colour palette. Follow nhs.uk page layout conventions.
- Do NOT use the NHS logo or imply this is an official NHS product. Show "Advolare - Clinical Trial Screening System" in the header.

If backend logic isn't ready, build the UI against mock JSON responses with the same shapes as `models.py`. Swap to real endpoints later without changing the pages.

## Mock data warehouse

Generated once by `data/generate.py` into `data/warehouse.db`, then left alone. Regenerate only when the schema or generator changes. Always use a fixed random seed so output is identical every run.

**Join keys:**
- `LocalPatientIdentifier` (MRN) — one per distinct patient. Joins patient-level tables.
- `EncounterID` — one per inpatient episode or ED attendance. Joins event-level tables.

**Tables** (columns provisional — confirm with the project owner before generating):

| Table | One row per | Key columns |
|---|---|---|
| Patients | distinct patient | LocalPatientIdentifier, DateOfBirth, Sex, EthnicCategory, PostcodeDistrict |
| PatientContacts | distinct patient | LocalPatientIdentifier, MobileNumber, Landline, Email, GuardianName, GuardianPhone |
| Inpatients | inpatient episode | EncounterID, LocalPatientIdentifier, AdmissionDateTime, DischargeDateTime, AdmissionMethod, Specialty, Ward |
| ECDS | ED/UTC attendance | EncounterID, LocalPatientIdentifier, CheckInDateTime, Acuity, ChiefComplaint, DischargeDestination |
| Observations | set of vital signs | EncounterID, RecordedDateTime, RespRate, SpO2, OnOxygen, Temperature, SystolicBP, DiastolicBP, HeartRate, Consciousness |
| Pathology | test result | EncounterID, LocalPatientIdentifier, TestCode, TestName, Value, Units, RefLow, RefHigh, CollectedDateTime |
| Diagnoses | diagnosis | EncounterID, LocalPatientIdentifier, ICD10Code, Description, Position (primary/secondary) |
| Procedures | procedure | EncounterID, LocalPatientIdentifier, OPCSCode, Description, ProcedureDateTime |
| ClinicalNotes | note | NoteID, EncounterID, LocalPatientIdentifier, NoteType (triage, clerking, ward round, nursing, discharge), AuthorRole, CreatedDateTime, NoteText |

Rules:
- **PatientContacts is never passed to any LLM and never exposed by any agent tool.** It is joined only after clinician approval.
- Fake contact details must be safe: mobiles from Ofcom's drama range `07700 900000`–`07700 900999`; emails at `example.com`.
- Children (under 16) have guardian contact details populated.
- Every row in every table has a unique, citable ID.

**How the data is generated:**
- **Faker (`en_GB`)**: names, dates of birth, postcodes, contact details only.
- **Rule-based logic**: clinical values must be coherent within each patient. Diagnosis drives vitals, vitals drive NEWS2, condition drives labs, acuity drives length of stay. Pneumonia → raised respiratory rate, raised CRP and white cells, possible raised urea.
- **Fixed code lists**: a curated subset of real ICD-10 and OPCS-4 codes relevant to the demo conditions plus common comorbidities.
- **LLM-generated notes**: written FROM each patient's structured record, in UK clinical style (abbreviations, fragments, negations, uncertainty). Notes must not contradict structured data unless deliberately planted in `hero_patients.py`.
- **Hero patients**: 8–12 hand-built patients covering clear matches, near-misses, and exclusions detectable only in notes (negation traps, time traps, medications mentioned only in notes, capacity / palliative status). Their correct answers go in `eval/labels.json`.
- Background volume: a few hundred patients is enough.

## LLM pipeline

- Served locally via an OpenAI-compatible endpoint. All calls go through `src/llm.py`. Model names live in `config.py` only — never hardcode them elsewhere.
- Installed models: `nemotron-3-nano:30b`, `nemotron-3.5-lightning:30b`, `qwen3.6:35b-a3b`.
- Default roles (configurable):
  - Screener (stage 1): `nemotron-3.5-lightning:30b` (tool use)
  - Verifier (stage 2): `qwen3.6:35b-a3b` (different model family, for an independent check)
- Temperature 0, fixed seed.
- Each model call answers ONE criterion for ONE patient.
- Output must be valid JSON matching `Verdict` in `models.py`. Use the server's structured / JSON-schema output. Reject and retry once on invalid output; on a second failure, return `insufficient`.
- Verdicts: `met`, `not_met`, `insufficient`. `insufficient` is valid and must never be forced into a guess.
- **Every verdict must include at least one citation** pointing to a specific record:
  ```json
  {"table": "ClinicalNotes", "record_id": "N-1042", "field": "NoteText", "quote": "home O2 2L via concentrator"}
  ```
- Stage 1 is tuned for recall: reject a patient only when an exclusion is clearly met with a citation. `insufficient` passes through to stage 2.
- Stage 2 reads the ORIGINAL records itself. It must not rely on stage 1's reasoning, only on stage 1's pointers to where to look.
- The tool loop has a hard step limit (see `config.py`).

## Validation layer

Code only. Runs after both stages. Never removes a patient; only adds flags.

For every citation:
1. The record exists in the cited table.
2. The record belongs to the patient being assessed.
3. For notes: the quote appears in the cited note text (case- and whitespace-insensitive match).
4. For structured fields: the quoted value matches the stored value.

Any failed check → flag the verdict as `citation_failed` and mark the patient `needs review`. Also flag when stage 1 and stage 2 disagree.

## Decision rules (`decide.py`)

- All inclusions `met` + no exclusions `met` + no flags → **Likely eligible**
- Any exclusion `met` by both models with valid citations → **Excluded** (with reasons)
- Anything else (any `insufficient`, any flag, any disagreement) → **Needs review**

## Rules for coding agents

1. Do only the task you are given. Edit only the files named in the task.
2. Do not change `src/models.py` or `data/schema.sql` without explicit approval.
3. No new dependencies without asking.
4. Never send data to any external service or API.
5. Never pass `PatientContacts` data to a model or expose it through a tool.
6. Write or update tests for any logic you add. Run `pytest` before saying you're done.
7. Prefer simple, readable code over clever code.
8. If something in this file seems wrong or blocks you, stop and ask rather than working around it.

## Commands

```
pip install -r requirements.txt
python data/generate.py              # build warehouse.db
uvicorn api.main:app --reload --port 8000
python scripts/run_cli.py            # run a screening from the terminal
python eval/evaluate.py              # evaluation metrics
pytest
```
