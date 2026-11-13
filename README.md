Advolare

Clinical trial screening on a single NVIDIA DGX Spark. All data is synthetic. See AGENTS.md.

## Run the web app

```
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

Open http://localhost:8000. The pages are plain HTML and vanilla JavaScript using the NHS.UK frontend
(vendored in `web/static/nhsuk/`). They talk to the JSON API under `/api` (docs at `/docs`), so buttons
never reload the page. Screening progress streams live from `GET /api/runs/{run_id}/events` (server-sent events).

Until `src/pipeline.py` exists, `api/main.py:_run_pipeline` returns mock results from `api/mock_data.py`
with the shapes in `src/models.py`. `ADVOLARE_MOCK_STEP_DELAY` sets the pause between mock steps (default 0.8s).

## Tests

```
pytest -q
```

## Docker

```
docker build -t trialmatch-web . && docker run -p 8000:8000 trialmatch-web
```

On the Spark, `deploy/` builds this Dockerfile as the `web` service and serves it at port 8000, with
`OPENAI_BASE_URL`, `SCREENER_MODEL` and `VERIFIER_MODEL` set from the compose file.
