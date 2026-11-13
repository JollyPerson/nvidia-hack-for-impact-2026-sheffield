# Advolare web app: FastAPI + NHS.UK frontend, served on port 8000.
# Built by deploy/ (compose service "web") on the DGX Spark. Settings come from the environment (see config.py).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# The synthetic warehouse is generated, not committed (seeded, so identical every build; about 2 seconds),
# then the LLM-written notes in data/notes_cache.json are matched to their encounters (data/load_notes.py).
# The app database (studies, runs, decisions, audit) is created and seeded on start-up.
RUN python data/generate.py && python data/load_notes.py

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
