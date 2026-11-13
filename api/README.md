# api/

The FastAPI layer between the browser (`web/`) and the pipeline (`src/`). AGENTS.md: "Connect the pipeline to the browser".
It serves the JSON API under `/api` (interactive docs at `/docs`) and the three HTML pages.

| File | What it owns | Lifetime |
|---|---|---|
| `main.py` | Every HTTP endpoint, the in-memory store (studies, runs, decisions, audit log) and the live progress stream (`GET /api/runs/{run_id}/events`, server-sent events). | Permanent |
| `schemas.py` | Shapes only the browser needs, built on `src/models.py` without changing it: `PatientView` (wraps `PatientResult` with age, sex, summary and the clinician's decision), `Funnel`, `TraceStep`, `ScreeningRun`, `Contact`, request/response bodies. Nothing in `src/` imports these. | Permanent |
| `mock_data.py` | Synthetic studies and screening results with the shapes of `src/models.py`, so the UI works before the pipeline exists. | **Temporary**: delete once the real pipeline is wired in |
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

Values are `true`/`false` (also `1`/`0`, `yes`/`no`, `on`/`off`). Empty or anything else keeps the default.
When an extra is off, the pages hide it (they read `GET /api/features`) and its API routes return 404.
Flags are read on every request, so a restart picks up a change; no rebuild is needed.

- **Locally:** `FEATURES_ALL=true uvicorn api.main:app --port 8000`
- **On the Spark:** uncomment lines in `deploy/features.env` and push to master (the deploy workflow redeploys),
  or put the same lines in `~/trialmatch/features.env` on the Spark and rerun `bash ~/trialmatch/deploy/deploy.sh`.
  `deploy/docker-compose.yml` passes them into the `web` container.

## Wiring in the real pipeline

1. Replace `_run_pipeline(study)` in `main.py` with a call to `src.pipeline`. It must return the run's `PatientView` list, funnel counts and trace steps.
2. Stream real progress: `run_events` currently replays the mock trace with a delay (`ADVOLARE_MOCK_STEP_DELAY`). Yield one `step` event per stage as the pipeline finishes it.
3. Read age and sex from `Patients`, and contact details from `PatientContacts` only inside `_contact_if_approved`.
4. Delete `mock_data.py`. The pages in `web/` need no changes.

## Rules this layer keeps (from AGENTS.md)

- `PatientContacts` is never sent to a model and is returned only after the clinician approves a patient (403 before). The same applies to the invitation letter.
- The API never makes the eligibility decision. It shows what `decide.py` returns and records the clinician's decision.
- No data leaves the device. No external calls.
