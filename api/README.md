# api/

The FastAPI layer between the browser (`web/`) and the pipeline (`src/`). AGENTS.md: "Connect the pipeline to the browser".
It serves the JSON API under `/api` (interactive docs at `/docs`) and the three HTML pages.

| File | What it owns | Lifetime |
|---|---|---|
| `main.py` | Every HTTP endpoint. Runs `src.pipeline.run_screening` in a background thread per screening and streams its progress (`GET /api/runs/{run_id}/events`, server-sent events). Studies, runs, decisions and the audit log go through `src/store.py` (app database). | Permanent |
| `schemas.py` | Shapes only the browser needs, built on `src/models.py` without changing it: `PatientView` (wraps `PatientResult` with age, sex, summary and the clinician's decision), `RunView` (wraps `ScreeningRun` with the funnel and progress steps), `Funnel`, `TraceStep`, `Contact` (mirrors `PatientContacts`), request/response bodies. Nothing in `src/` imports these. | Permanent |
| `mock_data.py` | Canned screening results with the shapes of `src/models.py`, used only when `ADVOLARE_MOCK=true`; also the free-text criteria drafter. | Fallback for demos without the Spark |
| `features.py` | Feature flags for the extras, read from environment variables (below). | Permanent |
| `extras.py` | Logic for the extra features: do-not-contact counts, fairness check, what-if, invitation letter, FHIR R5 export with SNOMED CT codes, screening log. Endpoints for them are in `main.py`. | Permanent (mock baselines inside are marked) |

## Switching the extras on and off

The core flow from AGENTS.md is always on: browse studies, create a study, run screening, results, evidence,
approve or reject, contact details after approval, and the audit trail. Each extra has a flag:

| Flag | Default | What it switches |
|---|---|---|
| `FEATURE_DO_NOT_CONTACT` | on | Patients with a do-not-contact flag are removed before screening and counted above the funnel |
| `FEATURE_DRAFT_CRITERIA` | on | "Describe who you need" box that drafts criteria rows from free text |
| `FEATURE_FAIRNESS` | off | Fairness check tab (shortlist demographics compared with admitted patients) |
| `FEATURE_WHAT_IF` | off | What if tab (shortlist without each criterion, from cached verdicts) |
| `FEATURE_SCREENING_LOG` | off | Screening log tab and CSV download |
| `FEATURE_INVITATION_LETTER` | off | Draft invitation letter after a patient is approved |
| `FEATURE_FHIR_EXPORT` | off | SNOMED CT column on the criteria table and the FHIR R5 download |
| `FEATURES_ALL` | unset | `true` or `false` sets every extra at once; a `FEATURE_` flag still wins over it |
| `ADVOLARE_DEV_MODE` | on | Footer toggles for each extra (developer mode, below) |

Values are `true`/`false` (also `1`/`0`, `yes`/`no`, `on`/`off`). Empty or anything else keeps the default.
When an extra is off, the pages hide it (they read `GET /api/features`) and its API routes return 404.
Flags are read on every request, so a restart picks up a change; no rebuild is needed.

- **In the browser (developer mode):** while `ADVOLARE_DEV_MODE` is on (the default for now), every page's footer
  has a checkbox per extra and a "Use deploy settings" button. A toggle is held in memory by the server: it wins over
  the env flags, applies to everyone using that server, changes the page in place, and is lost on restart.
  Set `ADVOLARE_DEV_MODE=false` to hide the toggles; `/api/dev/features` then returns 404.
- **Locally:** `FEATURES_ALL=true uvicorn api.main:app --port 8000`
- **On the Spark:** uncomment lines in `deploy/features.env` and push to master (the deploy workflow redeploys),
  or put the same lines in `~/trialmatch/features.env` on the Spark and rerun `bash ~/trialmatch/deploy/deploy.sh`.
  `deploy/docker-compose.yml` passes them into the `web` container.

## Screening: real pipeline or mock

| Setting | Default | What it does |
|---|---|---|
| `ADVOLARE_MOCK` | `false` | `true` uses the canned results in `mock_data.py` (no warehouse or model server needed) |
| `ADVOLARE_MAX_CANDIDATES` | `5` | Screen at most this many prefilter candidates per run, most recently admitted first. Each costs about 2 model calls per notes criterion. `0` = all |
| `ADVOLARE_WORKERS` | `4` | Patients screened at once (threads; each opens its own read-only warehouse connection) |
| `STAGE1_STOP_ON_EXCLUSION` | `0` (`1` in compose) | `config.py`: stage 1 stops at the first exclusion that is met with real evidence |
| `LLM_REASONING` / `SCREENER_REASONING` / `VERIFIER_REASONING` | `off` | `on` lets that model think (no `reasoning_effort` sent, up to `REASONING_MAX_OUTPUT_TOKENS`=4096 output tokens). Off always sends `reasoning_effort: "none"`. The developer-mode footer has a toggle per model |
| `ADVOLARE_MOCK_STEP_DELAY` | `0.8` | Seconds between mock progress steps |
| `APP_DB_PATH` | `data/app.db` | App database (`src/store.py`). Created and seeded with the demo studies on start-up |
| `DB_PATH` | `data/warehouse.db` | Warehouse (`config.py`). Build it with `python data/generate.py && python data/load_notes.py` (notes from `data/notes_cache.json`); the Docker image does this |
| `LLM_BASE_URL` / `OPENAI_BASE_URL` | `http://127.0.0.1:11434/v1` | Model server (`config.py`) |

A run: `POST /api/studies/{id}/runs` saves a `running` ScreeningRun and starts a thread that calls
`src.pipeline.run_screening`. The pipeline reports each step (running, then done with seconds); the SSE stream
forwards them and ends with `complete`, or `failed` with the error. The finished run is saved with `store.save_run`.
Age and sex come from `Patients`; contact details from `PatientContacts`, read only inside `_contact_if_approved`.
The summary line is the decide step's reasons.

## Rules this layer keeps (from AGENTS.md)

- `PatientContacts` is never sent to a model and is returned only after the clinician approves a patient (403 before). The same applies to the invitation letter.
- The API never makes the eligibility decision. It shows what `decide.py` returns and records the clinician's decision.
- No data leaves the device. No external calls.
