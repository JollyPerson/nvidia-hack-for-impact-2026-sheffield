"""Feature flags for the extras beyond the AGENTS.md core. Read from environment variables at request time.

    FEATURES_ALL=true|false       turns every extra on or off (overrides the defaults below)
    FEATURE_<NAME>=true|false     turns one extra on or off (overrides FEATURES_ALL), e.g. FEATURE_FAIRNESS=true

Unset or empty variables are ignored. A switched-off feature is hidden in the pages and its API routes return 404.
The core flow (browse, create, run, results, evidence, approve, contact after approval, audit) is always on.
"""
import os

from fastapi import HTTPException

# name -> (default, what it does). Do-not-contact and the criteria helper are on by default; the rest are opt-in.
FEATURES: dict[str, tuple[bool, str]] = {
    "do_not_contact": (True, "Remove patients with a do-not-contact flag before screening and show the count"),
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


def enabled(name: str) -> bool:
    default, _ = FEATURES[name]
    one, all_ = _env_bool(f"FEATURE_{name.upper()}"), _env_bool("FEATURES_ALL")
    return one if one is not None else all_ if all_ is not None else default


def flags() -> dict[str, bool]:
    return {name: enabled(name) for name in FEATURES}


def require(name: str):
    """FastAPI dependency: 404 when the feature is switched off, as if the route did not exist."""
    if name not in FEATURES:
        raise ValueError(f"Unknown feature: {name}")

    def check():
        if not enabled(name):
            raise HTTPException(404, "Not Found")
    return check
