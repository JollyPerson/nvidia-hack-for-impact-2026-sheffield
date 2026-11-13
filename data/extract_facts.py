"""Extract patient facts from every clinical note ONCE, into PatientFacts in warehouse.db (src/facts.py).

Run from the project root, after data/load_notes.py, with the model server up:

    python data/extract_facts.py                 # every note not yet extracted, 8 at a time
    python data/extract_facts.py --workers 4     # fewer parallel calls
    python data/extract_facts.py --limit 20      # try a few notes first
    python data/extract_facts.py --redo          # re-extract every note (e.g. after changing FACTS)
    python data/extract_facts.py --from-cache    # no model: load data/facts_cache.json (Dockerfile does this)

Every answer is also saved to a cache file (--cache, default data/facts_cache.json) keyed by NoteID and a
hash of the note text, so a rebuilt warehouse can reload the facts without the model. Commit that file.

One screener-model call per note (reasoning off, JSON schema). Code checks every quote against the note
before storing it; a failed call or a quote not found in the note is stored as error / unverified, and that
patient's criterion is then screened live. Safe to stop and rerun: it resumes with the notes still missing.
Uses OPENAI_BASE_URL / SCREENER_MODEL like the web app (config.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `python data/extract_facts.py` finds src

import config  # noqa: E402
from src import facts  # noqa: E402
from src.llm import LLM  # noqa: E402


CACHE = Path(__file__).resolve().parent / "facts_cache.json"


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(path: Path, cache: dict) -> None:
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(cache, indent=0, sort_keys=True, ensure_ascii=False))
    tmp.replace(path)


def load_cache(db: Path | str = config.DB_PATH, cache: Path | str = CACHE) -> int:
    """No model: insert cached answers for notes whose text is unchanged. Returns notes loaded."""
    data = _read_cache(Path(cache))
    conn = sqlite3.connect(str(db))
    try:
        facts.init_table(conn)
        n = 0
        with conn:
            for note_id, pid, text in conn.execute(
                    "SELECT NoteID, LocalPatientIdentifier, NoteText FROM ClinicalNotes").fetchall():
                hit = data.get(note_id)
                if not hit or hit.get("sha") != _sha(text):
                    continue
                conn.executemany("INSERT OR REPLACE INTO PatientFacts VALUES (?,?,?,?,?,?,?)",
                                 [(note_id, pid, k, v, q, hit.get("model"), hit.get("at", ""))
                                  for k, (v, q) in hit["facts"].items()])
                n += 1
        return n
    finally:
        conn.close()


def pending_notes(conn: sqlite3.Connection, redo: bool = False) -> list[tuple]:
    """(NoteID, patient, NoteText) for notes missing any fact or with an untrusted extraction."""
    rows = conn.execute("SELECT NoteID, LocalPatientIdentifier, NoteText FROM ClinicalNotes ORDER BY NoteID").fetchall()
    if redo:
        return rows
    good = Counter(r[0] for r in conn.execute(
        f"SELECT NoteID FROM PatientFacts WHERE Value IN ({','.join('?' * len(facts.VALUES))})", facts.VALUES))
    return [r for r in rows if good[r[0]] < len(facts.FACTS)]


def run(db: Path | str = config.DB_PATH, llm: LLM | None = None, workers: int = 8, limit: int | None = None,
        redo: bool = False, log=print, cache: Path | str | None = None) -> Counter:
    conn = sqlite3.connect(str(db), check_same_thread=False)
    try:
        facts.init_table(conn)
        todo = pending_notes(conn, redo)[: limit or None]
        llm = llm or LLM()
        counts: Counter = Counter()
        saved = _read_cache(Path(cache)) if cache else {}
        log(f"{db}: extracting {len(facts.FACTS)} facts from {len(todo)} notes, {workers} at a time "
            f"({config.SCREENER_MODEL} at {config.LLM_BASE_URL})")
        start = time.perf_counter()

        def one(row):
            note_id, _pid, text = row
            try:
                return row, facts.extract_note(llm, text)
            except Exception as e:  # noqa: BLE001 - one failed call must not stop the ingest
                return row, {f.key: (facts.ERROR, type(e).__name__) for f in facts.FACTS}

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(one, r) for r in todo]
            for n, fut in enumerate(as_completed(futures), 1):
                (note_id, pid, _text), answers = fut.result()
                now = datetime.now().isoformat(timespec="seconds")
                with conn:  # written from this (main) thread only
                    conn.executemany(
                        "INSERT OR REPLACE INTO PatientFacts VALUES (?,?,?,?,?,?,?)",
                        [(note_id, pid, k, v, q, config.SCREENER_MODEL, now) for k, (v, q) in answers.items()])
                counts.update(v for v, _ in answers.values())
                if cache:
                    saved[note_id] = {"sha": _sha(_text), "model": config.SCREENER_MODEL, "at": now,
                                      "facts": {k: [v, q] for k, (v, q) in answers.items()}}
                if n % 25 == 0 or n == len(todo):
                    if cache:
                        _write_cache(Path(cache), saved)
                    rate = n / max(time.perf_counter() - start, 1e-6)
                    log(f"  {n}/{len(todo)} notes, {rate:.1f}/s, ~{(len(todo) - n) / rate:.0f}s left. "
                        + ", ".join(f"{k} {c}" for k, c in sorted(counts.items())))
        return counts
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=config.DB_PATH)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--redo", action="store_true")
    p.add_argument("--cache", type=Path, default=CACHE, help="JSON cache of answers (read and written)")
    p.add_argument("--from-cache", action="store_true", help="load the cache only, no model calls")
    a = p.parse_args()
    if not a.db.exists():
        sys.exit(f"{a.db} not found; build it with data/generate.py and data/load_notes.py")
    if a.from_cache:
        print(f"{a.db}: PatientFacts loaded from {a.cache.name} for {load_cache(a.db, a.cache)} notes")
        return
    if not a.redo:
        load_cache(a.db, a.cache)  # reuse cached answers, then extract only what is still missing
    counts = run(a.db, workers=a.workers, limit=a.limit, redo=a.redo, cache=a.cache)
    bad = counts[facts.ERROR] + counts[facts.UNVERIFIED]
    print(f"done. {bad} answers untrusted (error/unverified): those patients' criteria are screened live.")


if __name__ == "__main__":
    main()
