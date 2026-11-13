"""Feature flags for the extras beyond the AGENTS.md core. Read from environment variables at request time.

    FEATURES_ALL=true|false       turns every extra on or off (overrides the defaults below)
    FEATURE_<NAME>=true|false     turns one extra on or off (overrides FEATURES_ALL), e.g. FEATURE_FAIRNESS=true

Unset or empty variables are ignored. A switched-off feature is hidden in the pages and its API routes return 404.
The core flow (browse, create, run, results, evidence, approve, contact after approval, audit) is always on.

Developer mode (ADVOLARE_DEV_MODE, on by default while we build) adds toggles in every page's footer. A toggle is a
runtime override held in memory for this server process: it wins over the env flags, applies to everyone using
this server, and is lost on restart. With ADVOLARE_DEV_MODE=false the toggles are hidden and their routes return 404.
"""
import os

from fastapi import HTTPException

# name -> (default, what it does). Do-not-contact and the criteria helper are on by default; the rest are opt-in.
FEATURES: dict[str, tuple[bool, str]] = {
    "do_not_contact": (True, "Remove patients with a do-not-contact flag before screening and show the count (from the next run)"),
    "draft_criteria": (True, "'Describe who you need' box that drafts criteria rows from free text"),
    "fairness": (False, "Fairness check tab: shortlist demographics compared with admitted patients"),
    "what_if": (False, "What if tab: shortlist size without each criterion, from cached verdicts"),
    "screening_log": (False, "Screening log tab and CSV download (ICH E6)"),
    "invitation_letter": (False, "Draft invitation letter after a patient is approved"),
    "fhir_export": (False, "SNOMED CT codes on criteria and FHIR R5 export of the study"),
}

_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name, "").strip().lower()
    return True if value in _TRUE else False if value in _FALSE else None


_overrides: dict[str, bool] = {}  # developer-mode toggles from the footer


def dev_mode() -> bool:
    value = _env_bool("ADVOLARE_DEV_MODE")
    return True if value is None else value


def _from_env(name: str) -> tuple[bool, str]:
    default, _ = FEATURES[name]
    one, all_ = _env_bool(f"FEATURE_{name.upper()}"), _env_bool("FEATURES_ALL")
    if one is not None:
        return one, f"FEATURE_{name.upper()}"
    if all_ is not None:
        return all_, "FEATURES_ALL"
    return default, "default"


def enabled(name: str) -> bool:
    if dev_mode() and name in _overrides:
        return _overrides[name]
    return _from_env(name)[0]


def describe() -> list[dict]:
    """For the developer toggles: each feature's state and where that state comes from."""
    rows = []
    for name, (_, label) in FEATURES.items():
        env_value, env_source = _from_env(name)
        overridden = dev_mode() and name in _overrides
        rows.append({"name": name, "label": label, "enabled": enabled(name), "env_enabled": env_value,
                     "source": "developer toggle" if overridden else env_source})
    return rows


def set_override(name: str, value: bool | None):
    """None removes the toggle so the env setting applies again."""
    if value is None:
        _overrides.pop(name, None)
    else:
        _overrides[name] = value


def clear_overrides():
    _overrides.clear()


def flags() -> dict[str, bool]:
    return {name: enabled(name) for name in FEATURES}


def require_dev_mode():
    if not dev_mode():
        raise HTTPException(404, "Not Found")


def require(name: str):
    """FastAPI dependency: 404 when the feature is switched off, as if the route did not exist."""
    if name not in FEATURES:
        raise ValueError(f"Unknown feature: {name}")

    def check():
        if not enabled(name):
            raise HTTPException(404, "Not Found")
    return check
