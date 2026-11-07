"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields). Every
number in it, the verdict, confidence and needs_human are computed here, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import batching
import merge
from checks import Comment
from diff_parse import FileDiff
from inputs import Asks
from severity import SEVERITIES

COMMENT_FIELDS = ("file", "line", "end_line", "severity", "category", "axis", "comment", "suggestion",
                  "pre_existing", "quote", "rule")


@dataclass(frozen=True)
class Counts:
    dropped: int = 0          # could not be tied to an added line
    formatting: int = 0       # formatting-only, skipped
    cut: int = 0              # over the caps
    failed_batches: int = 0


def comment_dict(c: Comment) -> dict:
    data = c.to_dict()
    return {k: data[k] for k in COMMENT_FIELDS}


def tally(comments: Sequence[dict]) -> str:
    parts = [f"{sum(1 for c in comments if c['severity'] == s)} {s}" for s in SEVERITIES]
    parts = [p for p in parts if not p.startswith("0 ")]
    return ", ".join(parts) if parts else "no comments"


def summary_line(verdict: str, comments: Sequence[dict], size: dict, tests: str | None) -> str:
    text = (f"{verdict} - {tally(comments)} across {size['files']} files "
            f"(+{size['added']} -{size['removed']}, {size['class']} size).")
    return f"{text} {tests}" if tests else text


def build(comments: Sequence[Comment], spec: dict | None, files: Sequence[FileDiff], plan: batching.Plan,
          stats: merge.Stats, counts: Counts, asks: Asks) -> dict:
    items = [comment_dict(c) for c in comments]
    verdict = merge.verdict(comments)
    size = merge.size_of(files)
    tests = merge.tests_note(files)
    level = merge.confidence(stats)
    notes = [n for n in (merge.size_note(size),) if n]
    if plan.partial:
        notes.append("Reviewed only the first hunks of: " + ", ".join(plan.partial) + " (size cap).")
    if counts.failed_batches:
        notes.append(f"{counts.failed_batches} part(s) of the diff could not be reviewed (model error).")
    if asks.spec_cut or asks.standards_cut:
        notes.append("The spec or standards were longer than I read; I used the first part.")
    if not asks.spec:
        notes.append("No spec given, so I did not check the change against requirements.")
    return {
        "summary": summary_line(verdict, items, size, tests), "items": items, "comments": items, "spec": spec,
        "verdict": verdict, "size": size, "skipped": list(plan.skipped), "tests_note": tests,
        "confidence": level, "needs_human": merge.needs_human(verdict, comments, spec, level),
        "dropped": counts.dropped, "formatting_skipped": counts.formatting, "over_cap": counts.cut,
        "notes": notes, "standards_given": bool(asks.standards),
    }
