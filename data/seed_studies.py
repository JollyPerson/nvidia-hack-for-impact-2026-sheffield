"""Build data/app.db and insert the two demo studies (pneumonia and heart failure).

Run from the project root:

    python data/seed_studies.py            # create app.db if needed, add any missing demo study
    python data/seed_studies.py --reset    # delete app.db first (loses runs, decisions and audit log)

Safe to run more than once: a study that is already there is left alone.
All study details are fictional.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `python data/seed_studies.py` finds src

from src import store  # noqa: E402
from src.models import AuditEntry, Criterion, Study  # noqa: E402

C = Criterion

# Same as the PRISM-CAP study the pages already use (api/mock_data.py).
PNEUMONIA = Study(
    id="S-001",
    title="Shorter antibiotic courses for adults admitted with community-acquired pneumonia",
    short_title="PRISM-CAP",
    condition="Community-acquired pneumonia",
    study_design="Randomised controlled trial",
    phase="Phase 4",
    intervention="3 days versus 7 days of oral amoxicillin after clinical stability",
    primary_outcome="Clinical cure at day 28",
    target_sample_size=60,
    recruitment_start=date(2026, 10, 1),
    recruitment_end=date(2027, 9, 30),
    chief_investigator="Dr Amira Shah",
    sponsor="Sheffield Teaching Research Trust (mock)",
    ethics_reference="IRAS 999001 (mock)",
    status="recruiting",
    criteria=[
        C(id="C1", type="inclusion", kind="structured", description="Aged 16 or over",
          field="age", operator=">=", value=16),
        C(id="C2", type="inclusion", kind="structured", description="CURB-65 score of 2 or more on admission",
          field="curb65", operator=">=", value=2),
        C(id="C3", type="inclusion", kind="notes",
          description="Clinical and radiological diagnosis of community-acquired pneumonia within the last 24 hours"),
        C(id="C4", type="exclusion", kind="structured", description="eGFR below 30 in the last 90 days",
          field="egfr", operator="<", value=30, time_window_days=90),
        C(id="C5", type="exclusion", kind="notes", description="COPD on long-term home oxygen"),
        C(id="C6", type="exclusion", kind="notes",
          description="Immunosuppressed (chemotherapy, high-dose steroids or organ transplant)"),
        C(id="C7", type="exclusion", kind="notes",
          description="Lacks capacity to consent or is receiving end-of-life care"),
    ],
)

HEART_FAILURE = Study(
    id="S-002",
    title="Nurse-led diuretic titration at home after hospital admission for acute heart failure",
    short_title="HOMESTEP-HF",
    condition="Acute heart failure",
    study_design="Randomised controlled trial",
    phase=None,
    intervention="Six weeks of nurse-led home diuretic titration versus usual care",
    primary_outcome="Heart failure readmission or death within 90 days",
    target_sample_size=80,
    recruitment_start=date(2026, 9, 1),
    recruitment_end=date(2027, 8, 31),
    chief_investigator="Dr Helen Marsh",
    sponsor="Northern Cardiovascular Research Network (mock)",
    ethics_reference="IRAS 999004 (mock)",
    status="recruiting",
    criteria=[
        C(id="C1", type="inclusion", kind="structured", description="Aged 18 or over",
          field="age", operator=">=", value=18),
        C(id="C2", type="inclusion", kind="notes",
          description="Admitted with new or worsening heart failure needing intravenous diuretics"),
        C(id="C3", type="inclusion", kind="structured", description="NT-proBNP of 2000 ng/L or more in the last 7 days",
          field="ntprobnp", operator=">=", value=2000, time_window_days=7),
        C(id="C4", type="exclusion", kind="structured", description="eGFR below 20 in the last 90 days",
          field="egfr", operator="<", value=20, time_window_days=90),
        C(id="C5", type="exclusion", kind="structured", description="Systolic blood pressure below 90 on admission",
          field="systolic_bp", operator="<", value=90),
        C(id="C6", type="exclusion", kind="notes",
          description="Acute coronary syndrome as the cause of this admission"),
        C(id="C7", type="exclusion", kind="notes",
          description="Lacks capacity to consent or is receiving end-of-life care"),
    ],
)

DEMO_STUDIES = [PNEUMONIA, HEART_FAILURE]


def seed(db: Path, reset: bool = False) -> list[str]:
    """Create the database and add missing demo studies. Returns the ids added."""
    if reset:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
    store.init_db(db)
    added = []
    for study in DEMO_STUDIES:
        if store.get_study(study.id, db=db) is None:
            store.create_study(study, db=db)
            store.append_audit(
                AuditEntry(when=datetime.now(), study_id=study.id, who="seed script",
                           action="study_created", note=study.short_title or study.title),
                db=db,
            )
            added.append(study.id)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete app.db first")
    parser.add_argument("--db", type=Path, default=store.APP_DB_PATH, help="database path")
    args = parser.parse_args()

    added = seed(args.db, reset=args.reset)
    print(f"{args.db}: added {', '.join(added) if added else 'nothing (demo studies already present)'}")
    for study in store.list_studies(db=args.db):
        inc, exc = len(study.inclusion_criteria), len(study.exclusion_criteria)
        print(f"  {study.id}  {study.short_title:<12} {study.condition:<30} "
              f"{inc} inclusion, {exc} exclusion criteria")


if __name__ == "__main__":
    main()
