"""Pure: the paste fallback. Several CI log excerpts, each under a header line
`RUN <id> <conclusion> <sha> <date>`, become runs and attempts for the same extractor and signals.
Flakiness then rests on the pasted runs only (confidence low)."""

from __future__ import annotations

import re
from dataclasses import replace

import history
import records
import testids

_HEADER = re.compile(r"^\s*RUN\s+(\S+)\s+(success|failure|failed|passed|timed_out|cancelled)\s+([0-9a-f]{7,40})\s+"
                     r"(\d{4}-\d\d-\d\d(?:[T ][\d:]+Z?)?)\s*$", re.IGNORECASE | re.MULTILINE)
_CONCLUSION = {"failed": "failure", "passed": "success"}
PASTED = "pasted logs"
MAX_PASTE_CHARS = 100_000


def has_runs(text: str) -> bool:
    return bool(_HEADER.search(text or ""))


def parse(text: str) -> tuple[list[records.Run], list[history.Attempt]]:
    """Runs (one attempt each, in paste order; the same id twice means a rerun) and their failing tests."""
    body = (text or "")[:MAX_PASTE_CHARS]
    headers = list(_HEADER.finditer(body))
    runs, attempts, seen = [], [], {}
    for i, m in enumerate(headers):
        run_id, sha = m.group(1), m.group(3).lower()
        conclusion = _CONCLUSION.get(m.group(2).lower(), m.group(2).lower())
        date = m.group(4).replace(" ", "T")
        attempt = seen.get(run_id, 0) + 1
        seen[run_id] = attempt
        raw = body[m.end():headers[i + 1].start() if i + 1 < len(headers) else len(body)]
        runs.append(records.Run(run_id, PASTED, sha, PASTED, _iso(date), conclusion, "completed", "", attempt))
        failures = tuple(replace(f, line=testids.verbatim_line(f, raw)) for f in testids.failures(raw))
        if failures and conclusion in history.RED:
            attempts.append(history.Attempt(run_id, attempt, failures))
    return _merge(runs), attempts


def _iso(date: str) -> str:
    return date if "T" in date else f"{date}T00:00:00Z"


def _merge(runs: list[records.Run]) -> list[records.Run]:
    """A run pasted twice is one run whose latest attempt is the last one pasted."""
    by_id: dict[str, records.Run] = {}
    for r in runs:
        first = by_id.get(r.run_id)
        by_id[r.run_id] = replace(r, created_at=first.created_at) if first else r
    return list(by_id.values())
