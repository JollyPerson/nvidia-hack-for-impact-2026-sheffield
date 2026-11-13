# TrialMatch: build plan

**NVIDIA Hack for Impact, Sheffield. Fri 13 Nov 2026. Submit by 18:00.**
Track: **Public Services**.

## One-line pitch

Clinical trials fail to recruit because the criteria that matter ("no GP visit in 12 months", "history of autoimmune thyroiditis") are buried in free-text GP notes. TrialMatch is a local agent on a DGX Spark. It reads a trial's eligibility text, screens a GP practice's patient records, and gives a recruiter a ranked shortlist where every decision cites the line in the note it came from. **Patient data never leaves the box.**

## How we hit the judging criteria

| Criterion | What we show |
|---|---|
| Impact | Most trials miss their recruitment targets. A GP practice can screen its whole list for a trial in minutes instead of a nurse spending days on chart review. |
| Technical execution (agentic + local) | A multi-step agent with tool calls and a live trace, running an open model on the Spark. Show the throughput figure (notes/min) and show that no network is needed. |
| Feasibility | Inputs are a ClinicalTrials.gov ID and an EHR export (CSV/FHIR). A human approves every match. The data-governance story is on-prem in the practice or trust. |

## Architecture

```
ClinicalTrials.gov API ──► [1 Criteria agent] ──► criteria.json
                                                 (structured | free-text)
                                                        │
patients.csv (Synthea) ─► [2 Structured filter] ◄───────┤   pandas/SQL, no LLM
                                │ candidates            │
notes/ (GP free text) ─► [3 Evidence retriever] ◄───────┤   embeddings, top-k snippets per criterion
                                │                       │
                         [4 Notes judge agent] ◄────────┘   per patient × criterion → MET / NOT_MET / UNKNOWN + quote
                                │
                         [5 Ranker + explainer] ──► Streamlit UI (trace, shortlist, evidence, approve/reject)
```

Everything runs on the Spark. The LLM is served behind an OpenAI-compatible endpoint, so any team member's code calls `http://spark:8000/v1`.

## Stack

- **LLM serving:** vLLM (or the NIM container if it is quick to pull) with an OpenAI-compatible API. If vLLM causes trouble, fall back to Ollama.
  - **Main model:** a mid-size open instruct model that fits easily in 128 GB, e.g. Llama 3.3 70B Instruct in FP8/AWQ or gpt-oss-120b. It handles criteria parsing (one call per trial, so it can be slow).
  - **Fast model:** Nemotron Nano 9B v2 or Llama 3.1 8B Instruct for the per-patient notes judge, since that runs many times.
  - Decide by 10:45 depending on what downloads and loads fastest. If time is short, use a single 8B model for everything.
- **Embeddings:** a small sentence-embedding model (e.g. `nvidia/llama-nemoretriever` family or `bge-small`) with numpy cosine similarity. No vector DB is needed for about 200 patients.
- **Structured output:** JSON schema / guided decoding in vLLM (`guided_json`) so agent outputs always parse.
- **Agent glue:** plain Python. LangGraph only if someone already knows it well. No time to learn frameworks today.
- **UI:** Streamlit.
- **Language:** Python 3.11. One `requirements.txt`.

## Data

1. **Patients:** Synthea, about 200 patients, CSV export (`patients.csv`, `conditions.csv`, `observations.csv`, `encounters.csv`). Flatten to one row per patient: age, sex, height, weight, BMI, smoker, key condition codes, last encounter date.
   - Fallback if the Java setup is slow: generate the same table with a Python script using Faker and plausible distributions.
2. **GP notes:** generate 3 to 6 short consultation notes per patient with the fast model on the Spark, seeded from that patient's structured facts. **Deliberately plant niche facts that exist only in the notes**: last GP visit, family history, prior chemo, pregnancy plans, rare diagnoses, mentions like "pt declines statins", negations ("no hx of MI"), and abbreviations (hx, SOB, T2DM). Plant them so we know the ground truth for evaluation.
3. **Trials:** 3 real oncology trials from the ClinicalTrials.gov API v2 (`https://clinicaltrials.gov/api/v2/studies/{NCT_ID}`, field `protocolSection.eligibilityModule`). Pick ones with a mix of numeric criteria and fuzzy ones. Cache the JSON in `data/trials/` so the demo works offline.
4. **Ground truth:** a `labels.csv` of about 30 patients × 1 trial, filled in partly from the planted facts and partly by hand.

## Agent steps (contracts)

1. **Criteria agent:** `trial_text → [{id, text, type: inclusion|exclusion, kind: structured|free_text, field?, op?, value?}]`. Structured items map to columns (age, BMI, sex). Everything else is free text.
2. **Structured filter:** pure pandas. Logs how many patients each criterion removes, which feeds a funnel chart in the UI.
3. **Evidence retriever:** for each (patient, free-text criterion), return the top 3 note snippets by embedding similarity, plus a keyword match.
4. **Notes judge:** `(criterion, snippets) → {status, quote, confidence}`. Must quote verbatim or return UNKNOWN. Batch with async requests to keep the Spark busy.
5. **Ranker:** exclude anyone with an exclusion criterion MET. Score = inclusion criteria met, minus unknowns. Produce a one-line explanation per patient.

Every step writes to a shared `trace` list. The UI streams it so judges can see the agent work.

## Demo UI (Streamlit)

1. Paste an NCT ID (or pick from 3 cached trials), then press **Find patients**.
2. Live agent trace panel showing each step, tool call and timing.
3. Funnel: 200 patients → N after structured filter → M likely eligible.
4. Shortlist table: patient, score, status chips per criterion.
5. Click a patient to see each criterion with the highlighted quote from the note, plus **Approve / Reject** buttons.
6. Footer badge: "Model: X on DGX Spark · 0 bytes sent off-device · N notes/min".

## Evaluation (for the pitch)

On the 30 labelled patients, compare:
- **Keyword baseline** (ctrl-F style search over notes)
- **TrialMatch**

Report precision, recall and time per patient. One slide, one chart.

## Team split

**Decision needed: team size.** The split below is for 4. With 3 people, merge the Data and Eval roles. With 5, give the UI its own owner and have a fifth person own the pitch/video.

| Person | Owns | Morning | Afternoon |
|---|---|---|---|
| A: Infra + agents lead | Spark, model serving, criteria agent, notes judge | vLLM up, models loaded, endpoint shared by 10:45 | Notes judge batching, speed, prompt tuning |
| B: Data | Synthea, note generator, trial fetcher | Patients + trials cached by 11:30, notes by 12:30 | Labels, eval script, baseline |
| C: Pipeline | Structured filter, retriever, ranker, trace | Filter + retriever on stub data | Wire end to end, caching for demo |
| D: UI + pitch | Streamlit app, slides, video | UI on mocked JSON (no waiting for the backend) | Plug in real pipeline, pitch deck, backup video |

Agree the JSON contracts above at 10:15 so everyone can build against mocks in parallel.

## Timeline

| Time | Milestone |
|---|---|
| 09:55–10:15 | Agree plan, roles, JSON contracts. Ask organisers about the submission format. |
| 10:15–10:45 | Spark up. Model downloading. Repo skeleton. Synthea running. |
| 10:45 | **Checkpoint 1:** LLM endpoint answers. Trials cached. |
| 10:45–12:30 | Criteria agent, filter, retriever built on stubs. Notes generated. UI on mocks. |
| 12:30 | **Checkpoint 2:** one trial runs end to end on real data, however ugly. |
| 12:30–13:00 | Lunch. |
| 13:00–15:00 | Notes judge quality + speed, ranker, UI wired to real pipeline, trace streaming. |
| 15:00 | **Checkpoint 3: feature freeze.** No new features after this. |
| 15:00–16:00 | Eval vs baseline, cache demo results, fix bugs. |
| 16:00–17:00 | Slides, 2-minute backup video, README. |
| 17:00–17:30 | Two full rehearsals of the demo. |
| 17:30–18:00 | Buffer. Submit. |

## Scope cuts if behind (in this order)

1. Drop the second model; use the 8B for everything.
2. Drop embeddings; send all of a patient's notes to the judge (they're short).
3. Drop live NCT ID entry; use the 3 cached trials.
4. Drop the eval; show 3 hand-checked examples instead.

## Risks

- **Model download time on venue Wi-Fi.** Start the download first thing. Have an 8B model as a fallback.
- **Notes look obviously synthetic.** Give the generator a few realistic example notes in UK GP style.
- **Demo latency.** Pre-compute results for the demo trial and keep a "replay" mode.
- **Overclaiming.** Say clearly that this is decision support for recruiters, not an eligibility decision, and that the data is synthetic.

## Repo layout (proposed)

```
app/            streamlit UI
agent/          criteria.py, filter.py, retrieve.py, judge.py, rank.py, llm.py
data/           trials/, synthea/, notes/, labels.csv
scripts/        fetch_trials.py, gen_notes.py, eval.py
PLAN.md
```
