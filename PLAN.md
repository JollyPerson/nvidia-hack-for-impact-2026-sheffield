# TrialMatch: revised build plan (v2, 11:15)

**NVIDIA Hack for Impact, Sheffield. Fri 13 Nov 2026. Submit by 18:00.** Track: **Public Services**.
This version adopts Saif's architecture. Each decision below says *why*, so the team can defend it to judges.

## Pitch

Clinical trials fail to recruit because the criteria that matter ("not seen by GP in 12 months", "hx of autoimmune thyroiditis") are buried in free-text GP notes. TrialMatch lets a clinician at an NHS trust set up a trial. It finds patients with a SQL query over the trust's data warehouse, then a two-model LLM cascade on a DGX Spark reads every candidate's notes. A validation layer checks every citation against the real record, and the clinician gets a reviewed shortlist. **Patient data never leaves the box.**

## Architecture (Saif's flow)

```
[1] NHS-themed front end ── clinician creates trial (paste criteria or import NCT ID)
        │
[2] Criteria parser (LLM 2, once per trial) ─► criteria.json  (structured | free_text)
        │
[3] SQL builder ─► read-only SELECT ─► [4] Mock data warehouse (DuckDB)
        │                                         │
        │                     filtered patient rows + their notes
        ▼                                         ▼
[5] LLM cascade
     LLM 1 "fast prefill" : small model, reads every candidate's notes row by row,
                            one verdict per (patient, criterion) + quote, shortlists
     LLM 2 "fast decode"  : MoE model, reviews the shortlist only, finalises
                            the ranking, writes the manifest (explanations + citations)
        │
[6] Validation layer ─── every quote must exist in that patient's note; every
        │                record claim must match the warehouse; else → UNKNOWN
[7] Clinician review ─── approve / reject per patient → contact list for enrolment
```

## Decisions and why

### 1. Data warehouse: DuckDB with a fixed NHS-style schema
**Why:** NHS trusts hold this data in SQL warehouses, so showing a SQL step makes the feasibility story concrete: "this plugs into what you already have." DuckDB is a single file and a pip install, loads Synthea CSVs in one line, and has a read-only mode. Postgres would cost setup time for no demo benefit.

Schema, frozen at 11:45 (agree it before anyone writes queries):
```
patients(patient_id, nhs_number_mock, sex, birth_date, postcode_district, smoker)
observations(patient_id, date, code, name, value, unit)     -- height, weight, BMI, eGFR...
conditions(patient_id, onset_date, resolved_date, snomed_code, name)
encounters(patient_id, date, type)                           -- GP visits
notes(note_id, patient_id, date, author_role, text)          -- free-text GP notes
v_patient_summary(patient_id, age, sex, bmi, last_gp_visit, ...)  -- view used by the SQL builder
```

### 2. SQL generation: criteria → JSON → *compiled* SQL, not free-form LLM SQL
**Why:** SQL written by an LLM can be wrong or unsafe, and a wrong filter silently drops eligible patients, which is exactly the failure we're trying to fix. Instead, the parser emits structured criteria (`{field: "age", op: ">=", value: 18}`) and plain Python compiles them into a parameterised `SELECT` against `v_patient_summary`. That's deterministic, testable, and still "software constructs a SQL query" as in Saif's diagram.
- Connection opened with `read_only=True`. Only `SELECT` statements are run.
- **The UI shows the generated SQL** and how many patients each clause removes (the funnel). Judges can read and check it.
- **Stretch only:** LLM-written SQL for criteria the compiler can't map, validated with `sqlglot` (single SELECT, allowed tables only) before running.

### 3. Notes go to the LLMs, not to SQL
**Why:** the niche criteria are in free text, and SQL `LIKE` is just keyword search, which is our baseline to beat. SQL narrows the candidates on hard facts (age, BMI, sex, diagnosis codes). The LLMs read the notes for the rest.

### 4. The cascade: small model screens everyone, MoE model finalises
This is Saif's "fast prefill / fast decode" split. Here is the honest version for judges:
- **LLM 1 (prefill-heavy job):** reads *every* candidate's notes (lots of input tokens) and writes a tiny JSON verdict. The work is dominated by prefill, which is compute-bound and where the GB10 Blackwell GPU is strong. Use a **small model, e.g. Nemotron Nano 9B v2 or Llama 3.1 8B**, batched with async requests (vLLM continuous batching).
  **Why small:** it runs (candidates × criteria) times, so speed matters more than depth.
- **LLM 2 (decode-heavy job):** reads only the shortlist and writes long output (rankings, explanations, the manifest). Decode is limited by memory bandwidth (~273 GB/s on the Spark), so a dense 70B model would generate only a few tokens/s. Use an **MoE model such as gpt-oss-120b** (about 5B parameters active per token): large-model quality with small-model decode speed. It also parses the criteria once at the start.
  **Why this split:** expensive reasoning runs only on the ~10–20% of patients who survive LLM 1. It fits in 128 GB together (gpt-oss-120b ≈ 65 GB + 9B ≈ 18 GB in FP8, plus KV cache).
- **Fallback:** if gpt-oss-120b won't download or load by 12:30, use gpt-oss-20b or the same small model for both roles. The architecture and the story stay the same.
- **Say "prefill-heavy / decode-heavy *stage*"**, not "prefill model". It is accurate, and back it with the measured tokens/s per stage on the trace screen.

### 5. Drop the embedding retriever from v1
**Why:** Saif's flow has LLM 1 read the patient's notes row by row. With 3–6 short notes per patient that's 1–2k tokens, so the whole history fits in context. A retriever adds a component that can miss evidence and costs build time. It's the first thing to add if we scale to real records.

### 6. Validation layer is a separate step, not part of the prompt
**Why:** judges will ask "does it hallucinate?" Prompts can't guarantee that; code can. After the cascade:
- **Quote grounding:** each MET/NOT_MET must carry a quote that appears verbatim (whitespace-normalised) in *that patient's* note with that `note_id`. Otherwise downgrade to UNKNOWN.
- **Record grounding:** any fact the manifest states from structured data (age, BMI, last visit) is re-read from DuckDB and must match.
- **Manifest schema:** pydantic model; every patient on the manifest must have passed SQL and LLM 1.
- Log every downgrade. Headline: "**0 unverified citations reached the clinician** (N caught)."

### 7. Clinician review is the decision point
**Why:** feasibility and safety. This is decision support: the tool proposes and cites, and a clinician approves before anyone is contacted. UNKNOWN is shown as "needs checking", not hidden. The existing UI already has approve/reject.

### 8. Keep the existing Streamlit UI and its mock-data seam
**Why:** it's built and already NHS-styled (`app/`, screenshots in `docs/mockups/`). `app/mock_data.py` exposes `get_trials()`, `get_criteria()` and `get_run()`. The real pipeline returns the same shapes, so the UI and backend can be built in parallel. Additions needed:
- a **"Create trial" form** (title, criteria text or NCT ID), Saif's step 1
- a **SQL panel** on the trace tab showing the compiled query and the per-clause funnel
- **trace rows for LLM 1, LLM 2 and Validation**, each with tokens/s
- a **"Download manifest"** button (JSON/CSV)

### 9. Evaluation as in VALIDATION.md
**Why:** "it works on our demo" doesn't persuade judges. Planted facts give free ground truth. Precision/recall per criterion against a keyword baseline (which is what SQL `LIKE` or Ctrl-F does) is the headline chart. Broken down by negation, abbreviation, temporal and family-history distractors, it shows *why* the LLM beats keyword search. See [VALIDATION.md](VALIDATION.md). Ask now: the note generator must log planted facts **before** notes are generated.

## Data

1. **Patients:** Synthea (~200) loaded into DuckDB. Faker-based Python generator as a fallback if Java is slow.
2. **Notes:** generated per patient on the Spark (3–6 UK GP-style notes), planting niche facts and distractors. Writes `data/labels_planted.jsonl` at the same time. Use LLM 2 to write and LLM 1 to judge, to reduce circularity.
3. **Trials:** 3 oncology trials from the ClinicalTrials.gov API v2, cached in `data/trials/` for offline demos. The front end can also accept typed criteria.

## Team (4)

Suggested owners. Swap names as you like; Saif is the obvious owner of the NHS data side.

| Role | Owner | Owns | Done by 12:30 | Afternoon |
|---|---|---|---|---|
| A: Models + cascade | ? | vLLM serving both models, LLM 1 screener, LLM 2 finaliser and criteria parser, batching | Both endpoints answer, LLM 1 verdict on one patient | Cascade end to end, tokens/s per stage, prompt tuning |
| B: Data + eval | ? | Synthea, note generator with planted labels, trial cache, keyword baseline, eval script | Notes + `labels_planted.jsonl` generated | Eval numbers and chart (15:00–16:00) |
| C: Warehouse + SQL + validation | **Saif** | DuckDB schema and load, SQL compiler, read-only runner, validation layer | Schema frozen 11:45, SQL returns candidates for trial 1 | Validation layer, record grounding, manifest schema |
| D: Front end + pitch | ? | Streamlit additions (create trial, SQL panel, cascade trace, manifest download), slides, backup video | Create-trial form + SQL panel on mocks | Wire to real pipeline, slides, video |

**Contracts to agree at 11:30:** `criteria.json` (already in `mock_data.py`), the SQL builder's output (`{sql, params, funnel}`), the LLM 1 verdict (`{patient_id, criterion_id, status, quote, note_id, confidence}`), and the manifest format.

## Timeline (from 11:15)

| Time | Milestone |
|---|---|
| 11:15–11:30 | Read this plan together. Confirm owners. Keep anything already running (model downloads, Synthea). |
| 11:30–11:45 | Agree JSON contracts and freeze the DuckDB schema. |
| 11:45–12:30 | Warehouse loaded. SQL compiler on trial 1. LLM 1 on one patient. Notes generating. UI additions on mocks. |
| **12:30** | **Checkpoint 1:** SQL → candidates → LLM 1 verdicts on real data for one trial, however rough. gpt-oss-120b loaded or fallback chosen. |
| 12:30–13:00 | Lunch (keep note generation running). |
| 13:00–14:30 | LLM 2 finaliser + manifest. Validation layer. UI wired to `get_run()`. |
| **14:30** | **Checkpoint 2:** full flow from create trial to clinician review, end to end. |
| 14:30–15:00 | Bug fixes. Cache demo results for replay mode. |
| **15:00** | **Feature freeze.** |
| 15:00–16:00 | Eval vs keyword baseline. Throughput and stopwatch hand-screen numbers. |
| 16:00–17:00 | Slides, 2-minute backup video, README. |
| 17:00–17:30 | Two full demo rehearsals. |
| 17:30–18:00 | Buffer. Submit. |

## Cut list if behind (in order)

1. LLM 2 → use the small model for both stages (keep the two-stage flow).
2. NCT import → typed criteria plus the 3 cached trials only.
3. Record grounding → keep quote grounding only.
4. Per-style eval breakdown → overall precision/recall only.
5. Last resort: replay mode with pre-computed results for the live demo.

## Risks

- **Model downloads on venue Wi-Fi:** start the gpt-oss-120b download now. The small model is the fallback.
- **Two models sharing 128 GB:** set vLLM `--gpu-memory-utilization` per server (e.g. 0.55 and 0.2) so they don't fight over memory.
- **"Prefill/decode" challenged by judges:** use the stage wording above, backed by measured numbers.
- **Synthetic data:** say it clearly. Next step is real notes (n2c2 2018 cohort selection, needs a data-use agreement) and a silent pilot at a GP practice.

## Repo layout

```
app/                 Streamlit UI (exists); mock_data.py is the seam
pipeline/
  warehouse.py       DuckDB load + read-only connection
  sql_builder.py     criteria.json -> parameterised SELECT + funnel
  llm.py             OpenAI-compatible clients for LLM 1 and LLM 2
  criteria.py        LLM 2: eligibility text -> criteria.json
  screen.py          LLM 1: per patient x criterion verdicts
  finalise.py        LLM 2: shortlist -> ranked manifest
  validate.py        quote + record grounding, manifest schema
  run.py             orchestrates and returns get_run() shape
scripts/             fetch_trials.py, gen_notes.py, eval.py
data/                trials/, synthea/, warehouse.duckdb, labels_planted.jsonl
PLAN.md  VALIDATION.md
```
