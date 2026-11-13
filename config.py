"""One place for settings. Values can be overridden with environment variables (see deploy/)."""
import os

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "ollama")
SCREENER_MODEL = os.environ.get("SCREENER_MODEL", "nemotron-3.5-lightning:30b")
VERIFIER_MODEL = os.environ.get("VERIFIER_MODEL", "qwen3.6:35b-a3b")
DB_PATH = os.environ.get("ADVOLARE_DB", "data/warehouse.db")
STEP_LIMIT = int(os.environ.get("ADVOLARE_STEP_LIMIT", "8"))
TEMPERATURE = 0.0
SEED = 42
# Seconds between mock pipeline steps when streaming progress. 0 in tests.
MOCK_STEP_DELAY = float(os.environ.get("ADVOLARE_MOCK_STEP_DELAY", "0.8"))
