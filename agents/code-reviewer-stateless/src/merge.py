"""Pure: merge, order and cap the checked comments, then compute everything the reply states in code: the
verdict, the size class, the tests note, confidence and needs_human, and the envelope."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import batching
import severity as sev
from checks import Comment
from diff_parse import FileDiff

MAX_COMMENTS = 25
MAX_PER_FILE = 8
MAX_NITS = 5
SIZE_CLASSES = ((100, "good"), (300, "acceptable"), (1_000, "large"))
HIGH_SURVIVAL = 0.9
LOW_SURVIVAL = 0.6
_ASSERTION = re.compile(r"\bassert|\bexpect\(|\bt\.(?:Error|Fatal)|\brequire\.")


def merged(comments: Sequence[Comment]) -> list[Comment]:
    """(6) one comment per (file, line, category), keeping the highest severity."""
    best: dict[tuple[str, int, str], Comment] = {}
    for c in comments:
        key = (c.file, c.line, c.category)
        if key not in best or sev.RANK[c.severity] < sev.RANK[best[key].severity]:
            best[key] = c
    return list(best.values())


def ordered(comments: Sequence[Comment], files: Sequence[FileDiff]) -> tuple[list[Comment], int]:
    """(7) critical -> required -> optional -> nit, then file rank, then line; capped. (kept, cut)."""
    rank = {f.path: n for n, f in enumerate(batching.ranked(tuple(files)))}
    kept: list[Comment] = []
    per_file: dict[str, int] = {}
    nits = 0
    for c in sorted(comments, key=lambda c: (sev.RANK[c.severity], rank.get(c.file, 999), c.line)):
        if len(kept) >= MAX_COMMENTS or per_file.get(c.file, 0) >= MAX_PER_FILE:
            continue
        if c.severity == "nit" and nits >= MAX_NITS:
            continue
        nits += c.severity == "nit"
        per_file[c.file] = per_file.get(c.file, 0) + 1
        kept.append(c)
    return kept, len(comments) - len(kept)


def size_of(files: Sequence[FileDiff]) -> dict:
    added = sum(f.added_count for f in files)
    removed = sum(f.removed_count for f in files)
    changed = added + removed
    label = next((name for limit, name in SIZE_CLASSES if changed <= limit), "too large")
    return {"files": len(files), "added": added, "removed": removed, "class": label}


def size_note(size: dict) -> str | None:
    if size["class"] == "large":
        return "This is a large change; consider splitting it."
    if size["class"] == "too large":
        return "This change is too large to review well; I reviewed the highest-ranked files. Consider splitting it."
    return None


def tests_note(files: Sequence[FileDiff]) -> str | None:
    kinds = [batching.kind_of(f) for f in files]
    if "source" in kinds and "test" not in kinds:
        return "No test changes in this diff."
    return None


def lost_assertions(files: Sequence[FileDiff]) -> list[Comment]:
    """A test file that removes more assertions than it adds gets a `required` comment from code."""
    out: list[Comment] = []
    for f in files:
        if batching.kind_of(f) != "test" or not f.added:
            continue
        removed = sum(1 for t in f.removed if _ASSERTION.search(t))
        added = sum(1 for t in f.added.values() if _ASSERTION.search(t))
        if removed > added:
            line = _line_near_removed_assertion(f)
            out.append(Comment(f.path, line, None, f.added[line], "required", "test", "correctness",
                               f"Tests lost assertions: this file removes {removed} and adds {added}, so the tests "
                               "check less than before.", None))
    return out


def _line_near_removed_assertion(f: FileDiff) -> int:
    for hunk in f.hunks:
        if any(ln.kind == "-" and _ASSERTION.search(ln.text) for ln in hunk.lines):
            first = next((ln.new_no for ln in hunk.lines if ln.kind == "+"), None)
            if first is not None:
                return first
    return min(f.added)


def verdict(comments: Sequence[Comment]) -> str:
    levels = {c.severity for c in comments}
    if levels & {"critical", "required"}:
        return "request_changes"
    return "comment" if levels else "no_blocking_comments"


@dataclass(frozen=True)
class Stats:
    proposed: int = 0
    survived: int = 0
    repairs: int = 0
    skipped_files: int = 0
    partial_files: int = 0
    truncated: bool = False


def confidence(stats: Stats) -> str:
    rate = stats.survived / stats.proposed if stats.proposed else 1.0
    if rate < LOW_SURVIVAL or stats.truncated:
        return "low"
    if rate < HIGH_SURVIVAL or stats.repairs or stats.skipped_files or stats.partial_files:
        return "medium"
    return "high"


def needs_human(result_verdict: str, comments: Sequence[Comment], spec: dict | None, level: str) -> bool:
    statuses = {r.get("status") for r in (spec or {}).get("requirements", [])}
    return (result_verdict == "request_changes" or any(c.severity == "critical" for c in comments)
            or bool(statuses & {"missing", "partial"}) or level == "low")

