# TrialMatch: how to validate it today

**Goal:** show judges that matches are *correct* (accuracy against ground truth), *trustworthy* (every decision is grounded in the note) and *practical* (fast and local). Total build time is about 2 hours, mostly in the 15:00–16:00 eval slot. Steps 1 and 3 need small changes **now**.

## Ranked by value for effort

| # | Option | Value | Effort | Verdict |
|---|---|---|---|---|
| 1 | **Planted-fact ground truth** (the note generator logs what it planted) | Very high: free labels for every patient | 15 min, *if done before notes are generated* | **Do now** (Person B) |
| 2 | **Precision/recall per criterion vs keyword baseline** | Very high: the headline number | 45 min | **Do** (15:00) |
| 3 | **Evidence grounding check** (is the quote really in that patient's note?) | Very high: answers "does it hallucinate?" | 10 min | **Do now** (Person A, inside the judge) |
| 4 | **Throughput and funnel** (notes/min, time for 200 patients) | High: proves the local-compute value | 15 min (log timings) | **Do** |
| 5 | **Schema and input validation** (guided JSON + pydantic, criteria coverage) | Medium: robustness story, "0 parse failures" | 20 min | **Do** (mostly free with `guided_json`) |
| 6 | **About 10 human-written held-out notes** | High: breaks the "LLM graded its own homework" critique | 30 min for one person | **Do if a person is free after 15:00** |
| 7 | Run-to-run consistency (same input twice at temp 0, or 3 runs at temp 0.3) | Medium | 10 min of compute | Nice to have |
| 8 | Clinician review | High, but nobody is available | n/a | Recruiter Approve/Reject in the UI is the human-in-the-loop. Ask a judge with clinical background for 2 minutes if one exists |
| 9 | LLM-as-judge on the outputs | Low: circular, and judges won't trust it | 30 min | **Skip** |
| 10 | Public benchmarks (n2c2 2018 cohort selection, TREC Clinical Trials, TrialGPT) | High credibility | n2c2 needs a signed data-use agreement (not today). TREC/TrialGPT is a different task setup (2h+) | **Skip.** Put it on the "next steps" slide |

## 1. Planted-fact ground truth (tell Person B now)

When `gen_notes.py` plants a fact, it should also write a row to `data/labels_planted.jsonl`:

```json
{"patient_id": "p017", "fact": "last_gp_visit", "value": "2024-03-02", "polarity": "positive",
 "style": "temporal", "note_id": "p017_n3", "sentence": "Last seen in surgery Mar 2024 for ..."}
```

- Tag each planted fact with a **style**: `plain`, `negation` ("no hx of MI"), `abbreviation` (T2DM, SOB), `temporal` ("not seen for 14 months"), `family_history` (a "mum had breast ca" distractor, which is *not* the patient's own history).
- Plant **distractors on purpose**: family history, negated mentions, resolved conditions. These are what keyword search gets wrong and the agent should get right.
- Labels per (patient, criterion) = MET / NOT_MET / NOT_IN_NOTES. Map the 3 trials' free-text criteria to planted fact keys by hand (about 15 minutes).
- **Circularity risk:** if the same model writes and judges the notes, say so. Mitigate by generating with a *different* model from the judge (e.g. 70B writes, 8B judges), plus item 6.

## 2. Accuracy vs keyword baseline

Unit = (patient, free-text criterion) on about 30 patients × 1 trial (more if the planted labels cover all 200, which is free).

- **Keyword baseline:** criterion keywords plus a short synonym list; MET if any match in the patient's notes. That's what a nurse's Ctrl-F does.
- **Metrics:** precision and recall on MET, reported **overall and by style** (negation / abbreviation / temporal / family history). The expected story is that keyword search has a high false-positive rate on negation and family history and misses temporal criteria.
- **UNKNOWN** is not counted as an error. Report it as the "% sent to human review" rate.
- **Patient-level:** precision/recall of "eligible" against labels, and the funnel 200 → N → M.
- Be upfront in the pitch: "the test set deliberately includes the cases keyword search fails on." Judges respect that more than an unexplained 98%.

## 3. Evidence grounding check (inside the judge)

After each judge call, accept MET/NOT_MET only if the quote really appears in that patient's own notes. Otherwise downgrade to UNKNOWN and log it.

```python
import re
norm = lambda s: re.sub(r"\s+", " ", s.lower()).strip()
def grounded(quote, patient_notes):          # patient_notes: list[str]
    q = norm(quote)
    return len(q) >= 8 and any(q in norm(n) for n in patient_notes)
```

Report: "**X% of decisions carry a verbatim quote; 0 unverified quotes ever reach the recruiter** (Y caught and downgraded)." This is the strongest trust claim available and costs 10 lines.

## 4. Throughput

Log the start and end time of each step in the trace. Report notes/min for the judge, end-to-end time for 200 patients, and GPU utilisation from `nvidia-smi` during the batch. For a time comparison, **have a teammate hand-screen 5 patients against the trial with a stopwatch** rather than quoting a number from the internet.

## 5. Schema and input validation

- `guided_json` + a pydantic model per step. Count parse failures and retries (target 0).
- **Criteria coverage check:** every bullet in the trial's eligibility text maps to at least one parsed criterion. Hand-check all 3 trials once and show it ("3/3 trials, N/N criteria captured").
- Structured criteria: validate units and ranges (age 0–120, BMI 10–80, etc.). Reject the criteria JSON rather than silently filtering everyone out.

## Numbers for the judges (one slide plus the footer badge)

1. **Recall and precision on note-only criteria: TrialMatch vs keyword search** (bar chart, broken down by negation / abbreviation / temporal / family history).
2. **Grounding:** % of decisions with a verified verbatim quote; hallucinated quotes shown to the recruiter = 0.
3. **Human-review rate:** % UNKNOWN, framed as "the agent says when it doesn't know".
4. **Speed:** 200 patients screened in M minutes on one DGX Spark (N notes/min) vs about K minutes per patient by hand; 0 bytes off-device.
5. Caveat line: synthetic patients and notes; decision support with a human approving every match.

**Next steps slide:** validate on real notes via the n2c2 2018 cohort-selection data (needs a data-use agreement), then a silent pilot at a GP practice comparing against their existing recruitment search.
