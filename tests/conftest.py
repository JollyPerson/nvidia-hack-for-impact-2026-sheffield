import sys
from pathlib import Path

# Let tests import `config`, `src.*` and `scripts.*` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest as _pytest  # noqa: E402

import config as _config  # noqa: E402


@_pytest.fixture(autouse=True)
def _tool_loop_by_default(monkeypatch):
    """The scripted and rule-based fake models find evidence through tools, so tests use the tool loop
    unless they switch one-call checks on themselves."""
    monkeypatch.setattr(_config, "ONE_CALL_CHECKS", False)


@_pytest.fixture(autouse=True)
def _facts_on_in_tests(monkeypatch):
    """Facts are off by default in config (demo); tests of the facts path need them on."""
    monkeypatch.setattr(_config, "FACTS_ENABLED", True)
