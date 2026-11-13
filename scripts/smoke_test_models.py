"""Run the five-patient test against the REAL models on the Spark.

    source .venv/bin/activate
    python scripts/smoke_test_models.py            # all five patients
    python scripts/smoke_test_models.py P001       # just one, quicker

    # use the team's own Ollama, which keeps both models loaded (faster):
    LLM_BASE_URL=http://10.41.30.10:11434/v1 python scripts/smoke_test_models.py

Prints each verdict next to the correct answer and saves the full prompts and
outputs to logs/ so you can see what the models did.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("REFERENCE_DATE", "2026-11-13")  # the test notes are dated around this

import config  # noqa: E402
from scripts.five_patients import CASE, EXPECTED, STUDY, build_db  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.screening import screen_patient  # noqa: E402


def main() -> int:
    wanted = [a.upper() for a in sys.argv[1:]] or list(EXPECTED)
    print(f"Model server: {config.LLM_BASE_URL}")
    print(f"Screener:     {config.SCREENER_MODEL}")
    print(f"Verifier:     {config.VERIFIER_MODEL}\n")

    llm = LLM()
    try:
        available = {m.id for m in llm.client.models.list()}
    except Exception as e:  # noqa: BLE001 - show any connection problem plainly
        print(f"Cannot reach the model server: {e}")
        print("Is Ollama running? Try:  docker ps   (look for an ollama container)")
        return 1
    missing = [m for m in (config.SCREENER_MODEL, config.VERIFIER_MODEL) if m not in available]
    if missing:
        print(f"These models are not installed on the server: {missing}")
        print(f"Installed: {sorted(available)}")
        return 1

    conn = build_db()
    audit: list = []
    right = total = 0
    print(f"{'patient':8} {'case':28} {'crit':5} {'expected':13} {'stage 1':13} {'stage 2':13} ok")
    for pid in wanted:
        t0 = time.time()
        result = screen_patient(llm, conn, STUDY, pid, audit=audit)
        for a in result.assessments:
            want = EXPECTED[pid][a.criterion_id]
            s1 = a.stage1.verdict.value if a.stage1 else "-"
            s2 = a.stage2.verdict.value if a.stage2 else "-"
            final = s2 if a.stage2 else (s1 if a.stage1 else None)
            if want is not None:
                total += 1
                right += final == want
            mark = "" if want is None else ("YES" if final == want else "NO")
            print(f"{pid:8} {CASE[pid]:28} {a.criterion_id:5} {str(want or '-'):13} {s1:13} {s2:13} {mark}")
            if mark == "NO":
                why = (a.stage2 or a.stage1).reasoning
                print(f"{'':8} why: {why[:160]}")
        print(f"{'':8} shortlisted={result.shortlisted}  ({time.time() - t0:.0f}s)")

    print(f"\nCorrect: {right}/{total}   (stage 2 '-' means the patient was dropped at stage 1)")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"smoke_test_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    print(f"Full prompts and answers saved to {log_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
