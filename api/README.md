# api/

The FastAPI layer between the browser (`web/`) and the pipeline (`src/`). AGENTS.md: "Connect the pipeline to the browser".
It serves the JSON API under `/api` (interactive docs at `/docs`) and the three HTML pages.

| File | What it owns | Lifetime |
|---|---|---|
| `main.py` | Every HTTP endpoint, the in-memory store (studies, runs, decisions, audit log) and the live progress stream (`GET /api/runs/{run_id}/events`, server-sent events). | Permanent |
| `schemas.py` | Shapes only the browser needs, built on `src/models.py` without changing it: `PatientView` (wraps `PatientResult` with age, sex, summary and the clinician's decision), `Funnel`, `TraceStep`, `ScreeningRun`, `Contact`, request/response bodies. Nothing in `src/` imports these. | Permanent |
| `mock_data.py` | Synthetic studies and screening results with the shapes of `src/models.py`, so the UI works before the pipeline exists. | **Temporary**: delete once the real pipeline is wired in |
| `extras.py` | Logic for the extra features: do-not-contact counts, fairness check, what-if, invitation letter, FHIR R5 export with SNOMED CT codes, screening log. Endpoints for them are in `main.py`. | Permanent (mock baselines inside are marked) |

## Wiring in the real pipeline

1. Replace `_run_pipeline(study)` in `main.py` with a call to `src.pipeline`. It must return the run's `PatientView` list, funnel counts and trace steps.
2. Stream real progress: `run_events` currently replays the mock trace with a delay (`ADVOLARE_MOCK_STEP_DELAY`). Yield one `step` event per stage as the pipeline finishes it.
3. Read age and sex from `Patients`, and contact details from `PatientContacts` only inside `_contact_if_approved`.
4. Delete `mock_data.py`. The pages in `web/` need no changes.

## Rules this layer keeps (from AGENTS.md)

- `PatientContacts` is never sent to a model and is returned only after the clinician approves a patient (403 before). The same applies to the invitation letter.
- The API never makes the eligibility decision. It shows what `decide.py` returns and records the clinician's decision.
- No data leaves the device. No external calls.
