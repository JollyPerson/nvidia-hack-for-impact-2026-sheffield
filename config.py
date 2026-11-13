"""One place for settings (see AGENTS.md).

Every value can be overridden with an environment variable, so the same code
runs on the Spark directly (organiser Ollama on 127.0.0.1:11434) and inside the
docker-compose stack (http://ollama:11434/v1).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- Model server -----------------------------------------------------------
# Ollama's OpenAI-compatible endpoint. docker-compose sets LLM_BASE_URL /
# OPENAI_BASE_URL; outside docker we fall back to the Ollama on this machine
# (on the Spark that is now the team's own Ollama, with both models loaded).
LLM_BASE_URL = (
    os.getenv("LLM_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or "http://127.0.0.1:11434/v1"
)
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "ollama")  # Ollama ignores it
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))

# --- Model per role -----------------------------------------------------------
# Model names live here only. Never hardcode them anywhere else.
SCREENER_MODEL = os.getenv("SCREENER_MODEL", "nemotron-3.5-lightning:30b")
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "qwen3.6:35b-a3b")
MODELS = {"screener": SCREENER_MODEL, "verifier": VERIFIER_MODEL}

# --- Generation settings ------------------------------------------------------
TEMPERATURE = 0.0
SEED = 42
# Ask the server not to "think" out loud: thinking burns the 8k budget.
# Set REASONING_EFFORT="" to send nothing.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "none")
# Reasoning ("thinking") per role: off (default) sends REASONING_EFFORT ("none") on every call;
# on lets the model think. LLM_REASONING sets both; SCREENER_/VERIFIER_REASONING win over it.
# The web app's developer mode can override these at runtime (src/llm.py REASONING_OVERRIDES).
REASONING = {
    role: (os.getenv(f"{role.upper()}_REASONING") or os.getenv("LLM_REASONING") or "off").strip().lower()
    in {"on", "true", "1", "yes"}
    for role in ("screener", "verifier")
}
# Thinking uses output tokens before the answer, so allow more when it is on (Qwen came back
# empty at 300 tokens). The prompt budget shrinks to match (8,192 total per request).
# Stage 2 (the verifier model). ADVOLARE_VERIFIER=off screens with the screener model alone (single-model
# mode); the web app's developer mode can override it (src/llm.py STAGE_OVERRIDES).
VERIFIER_ENABLED = os.getenv("ADVOLARE_VERIFIER", "on").strip().lower() not in {"off", "false", "0", "no"}
REASONING_MAX_OUTPUT_TOKENS = int(os.getenv("REASONING_MAX_OUTPUT_TOKENS", "4096"))

# --- Token budget (organiser hard limit: 8,192 tokens per request) -------------
CONTEXT_TOKENS = 8192
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
# Prompt must fit in what is left. We estimate tokens as characters / 3,
# which over-counts for English, so the estimate errs on the safe side.
CHARS_PER_TOKEN = 3.0

# --- Agent tool loop ------------------------------------------------------------
# Stage 1 normally checks every criterion so the clinician sees the full picture.
# Set STAGE1_STOP_ON_EXCLUSION=1 to skip the rest once an exclusion is met (faster).
STAGE1_STOP_ON_EXCLUSION = os.getenv("STAGE1_STOP_ON_EXCLUSION", "0") == "1"
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "4"))  # tool rounds per criterion
MAX_NOTE_CHARS = 1200   # one note's text is cut to this in a tool result
MAX_NOTES_RETURNED = 4  # search_notes returns at most this many notes
# Each check starts with the patient's own notes in the prompt (newest first, whole notes only), so the
# model rarely needs to search: about 2 calls per check instead of about 5. Capped so the prompt still
# fits the 8k budget; 0 turns it off (the model then finds notes with search_notes only).
NOTES_IN_PROMPT_CHARS = int(os.getenv("NOTES_IN_PROMPT_CHARS", "8000"))
# When every note fits in the prompt, check a criterion in one model call (no tool loop). 0 = tool loop.
ONE_CALL_CHECKS = os.getenv("ONE_CALL_CHECKS", "1").strip().lower() not in ("0", "false", "no", "off")
MAX_ROWS_RETURNED = 15  # labs / observations / diagnoses rows per tool call

# --- Data -------------------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "warehouse.db"))
# "Today" for time windows. Fixed by env for reproducible demos; else real date.
REFERENCE_DATE = os.getenv("REFERENCE_DATE")  # e.g. "2026-11-13"
# Fixed "now" for the prefilter's ages and time windows. Matches the fixed
# "now" in data/generate.py, so the synthetic data never ages out of a window.
REFERENCE_NOW = datetime.fromisoformat(os.getenv("REFERENCE_NOW", "2026-11-13T09:00:00"))
