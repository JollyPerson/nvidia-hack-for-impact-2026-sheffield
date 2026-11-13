"""One place for settings (see AGENTS.md).

Every value can be overridden with an environment variable, so the same code
runs on the Spark directly (organiser Ollama on 127.0.0.1:11434) and inside the
docker-compose stack (http://ollama:11434/v1).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- Model server -----------------------------------------------------------
# Ollama's OpenAI-compatible endpoint. docker-compose sets LLM_BASE_URL /
# OPENAI_BASE_URL; outside docker we fall back to the organiser's Ollama.
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

# --- Token budget (organiser hard limit: 8,192 tokens per request) -------------
CONTEXT_TOKENS = 8192
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1024"))
# Prompt must fit in what is left. We estimate tokens as characters / 3,
# which over-counts for English, so the estimate errs on the safe side.
CHARS_PER_TOKEN = 3.0

# --- Agent tool loop ------------------------------------------------------------
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "4"))  # tool rounds per criterion
MAX_NOTE_CHARS = 1200   # one note's text is cut to this in a tool result
MAX_NOTES_RETURNED = 4  # search_notes returns at most this many notes
MAX_ROWS_RETURNED = 15  # labs / observations / diagnoses rows per tool call

# --- Data -------------------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "warehouse.db"))
# "Today" for time windows. Fixed by env for reproducible demos; else real date.
REFERENCE_DATE = os.getenv("REFERENCE_DATE")  # e.g. "2026-11-13"
