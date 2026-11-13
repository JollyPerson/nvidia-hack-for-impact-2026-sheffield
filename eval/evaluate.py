"""Recall, precision and per-criterion success rate of the screening pipeline, on hand-labelled patients.

    source .venv/bin/activate
    python eval/evaluate.py                # all 18 labelled patients, 4 at a time
    python eval/evaluate.py --workers 1    # one at a time

Runs the real pipeline steps after the SQL prefilter (stage 1 screener, stage 2 verifier, citation
validation, code decision) on eval/cases.py, against the real models. Structured criteria are plain SQL
(tested in tests/test_prefilter.py), so the cases are written to have passed them.

Definitions (patients whose eligibility a clinician could not decide either are left out, and reported apart):
  recall              = truly eligible patients the pipeline marks "likely eligible"
  recall incl. review = truly eligible patients the pipeline does NOT exclude (eligible or sent to review),
                        i.e. patients not lost to the trial
  precision           = "likely eligible" patients who are truly eligible
  exclusion precision = "excluded" patients who are truly ineligible
  per-criterion       = the final verdict (stage 2 when it ran, else stage 1) equals the label
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.cases import CASES, REFERENCE_DATE, build_db  # noqa: E402

os.environ.setdefault("REFERENCE_DATE", REFERENCE_DATE)  # the notes are dated around this day

import config  # noqa: E402
from data.seed_studies import HEART_FAILURE, PNEUMONIA  # noqa: E402
from src.decide import decide  # noqa: E402
from src.llm import LLM, verifier_on  # noqa: E402
from src.models import Citation, CriterionAssessment, CriterionKind, CriterionType, FinalStatus, Verdict  # noqa: E402
from src.screening import screen_patient  # noqa: E402
from src.tools import connect  # noqa: E402
from src.validation import validate_patient  # noqa: E402

STUDIES = {"PNEUMONIA": PNEUMONIA, "HEART_FAILURE": HEART_FAILURE}


def run_case(llm, db_path: str, case) -> dict:
    """Screen, validate and decide one labelled patient, exactly as pipeline.py does after the prefilter."""
    study = STUDIES[case.study]
    conn = connect(db_path)
    try:
        t = time.perf_counter()
        r = screen_patient(llm, conn, study, case.pid)
        validate_patient(conn, r)
        code_met = [  # structured inclusions: passed in SQL (pipeline._code_assessments)
            CriterionAssessment(criterion_id=c.id, stage1=Verdict(
                verdict="met", reasoning="Checked by code (SQL prefilter).",
                citations=[Citation(table="Patients", record_id=case.pid, field="DateOfBirth", quote=case.dob)]))
            for c in study.criteria if c.kind == CriterionKind.structured and c.type == CriterionType.inclusion
        ]
        r.assessments += code_met
        decide(study, r, single_model=not verifier_on())
        seconds = time.perf_counter() - t
    finally:
        conn.close()
    verdicts = {}
    for a in r.assessments:
        if a.criterion_id in case.labels:
            final = a.stage2 or a.stage1
            verdicts[a.criterion_id] = {
                "label": case.labels[a.criterion_id],
                "stage1": a.stage1.verdict.value if a.stage1 else None,
                "stage2": a.stage2.verdict.value if a.stage2 else None,
                "final": final.verdict.value if final else None,
                "flags": [f.type.value for f in a.flags],
                "reasoning": (final.reasoning if final else "")[:300],
            }
    return {"pid": case.pid, "study": case.study, "what": case.what, "eligible": case.eligible,
            "status": r.status.value, "reasons": r.reasons, "seconds": round(seconds, 1), "criteria": verdicts}


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} = {n / d:.0%}" if d else "n/a"


def metrics(rows: list[dict]) -> dict:
    decided = [r for r in rows if r["eligible"] is not None]
    pos = [r for r in decided if r["eligible"]]
    neg = [r for r in decided if not r["eligible"]]
    marked = [r for r in decided if r["status"] == FinalStatus.likely_eligible.value]
    excluded = [r for r in decided if r["status"] == FinalStatus.excluded.value]
    tp = sum(r["eligible"] for r in marked)

    per = defaultdict(lambda: [0, 0])
    stage1 = [0, 0]
    for r in rows:
        study = STUDIES[r["study"]]
        desc = {c.id: c.description for c in study.criteria}
        for cid, v in r["criteria"].items():
            key = f"{study.short_title} {cid}: {desc[cid]}"
            per[key][0] += v["final"] == v["label"]
            per[key][1] += 1
            stage1[0] += v["stage1"] == v["label"]
            stage1[1] += 1
    right = sum(n for n, _ in per.values())
    total = sum(d for _, d in per.values())
    return {
        "patients": len(rows),
        "recall": _pct(tp, len(pos)),
        "recall_incl_review": _pct(sum(r["status"] != FinalStatus.excluded.value for r in pos), len(pos)),
        "precision": _pct(tp, len(marked)),
        "exclusion_precision": _pct(sum(not r["eligible"] for r in excluded), len(excluded)),
        "eligible_wrongly_excluded": [r["pid"] for r in pos if r["status"] == FinalStatus.excluded.value],
        "ineligible_marked_eligible": [r["pid"] for r in neg if r["status"] == FinalStatus.likely_eligible.value],
        "undecidable_sent_to_review": _pct(
            sum(r["status"] == FinalStatus.needs_review.value for r in rows if r["eligible"] is None),
            sum(r["eligible"] is None for r in rows)),
        "criterion_success_overall": _pct(right, total),
        "criterion_success_stage1_only": _pct(*stage1),
        "per_criterion": {k: _pct(n, d) for k, (n, d) in per.items()},
    }


def report(rows: list[dict], m: dict) -> str:
    lines = [
        f"# Advolare evaluation, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Screener `{config.SCREENER_MODEL}`, verifier `{config.VERIFIER_MODEL if verifier_on() else 'off'}`. "
        f"{m['patients']} hand-labelled patients (eval/cases.py).",
        "",
        "## Headline",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Recall (eligible patients marked likely eligible) | {m['recall']} |",
        f"| Recall incl. review list (eligible patients not lost) | {m['recall_incl_review']} |",
        f"| Precision (likely-eligible patients truly eligible) | {m['precision']} |",
        f"| Exclusion precision (excluded patients truly ineligible) | {m['exclusion_precision']} |",
        f"| Undecidable cases sent to review | {m['undecidable_sent_to_review']} |",
        f"| Per-criterion success, final verdict | {m['criterion_success_overall']} |",
        f"| Per-criterion success, stage 1 alone | {m['criterion_success_stage1_only']} |",
        "",
        "## Per criterion",
        "",
        "| Criterion | Correct |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in m["per_criterion"].items()],
        "",
        "## Per patient",
        "",
        "| Patient | Case | Truly eligible | Pipeline says | Wrong criteria |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        wrong = ", ".join(f"{cid} (want {v['label']}, got {v['final']})"
                          for cid, v in r["criteria"].items() if v["final"] != v["label"]) or "-"
        truth = {True: "yes", False: "no", None: "unclear"}[r["eligible"]]
        lines.append(f"| {r['pid']} | {r['what']} | {truth} | {r['status']} | {wrong} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=4, help="patients screened at once")
    args = ap.parse_args()

    llm = LLM()
    print(f"Model server {config.LLM_BASE_URL} | screener {config.SCREENER_MODEL} | "
          f"verifier {config.VERIFIER_MODEL if verifier_on() else 'off'}")
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "eval.db")
        build_db(db).close()
        t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            rows = list(pool.map(lambda c: run_case(llm, db, c), CASES))
        wall = time.perf_counter() - t

    m = metrics(rows)
    text = report(rows, m) + f"\nRun time: {wall:.0f}s for {len(rows)} patients ({args.workers} at a time).\n"
    print("\n" + text)
    out = ROOT / "logs"
    out.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    (out / f"eval_{stamp}.md").write_text(text)
    (out / f"eval_{stamp}.json").write_text(json.dumps({"metrics": m, "patients": rows}, indent=2))
    print(f"Saved logs/eval_{stamp}.md and .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
