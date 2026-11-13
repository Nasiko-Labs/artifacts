"""Pure: follow-up views cut from a rendered review found in history, with no call. Only lines that are in
that earlier reply are shown, never recomputed or invented; what it does not hold (the score breakdown, the
JSON envelope) needs the PRD itself (`turns.py` reviews it again, or asks for a re-paste)."""

from __future__ import annotations

import re

VIEWABLE = ("blockers", "metrics", "requirements", "sections", "questions", "all")
QUESTION_HEADINGS = ("**Questions a reviewer will ask**", "**Questions the PRD already raises (unanswered)**")
MISSING = "**Missing or empty sections:**"
PARTIAL_NOTE = ("_The earlier review listed only part of some groups, so this may not be every gap; paste the PRD "
                "again to see them all._")

_GAP = re.compile(r"^(?:\d+\.|-) \[(blocker|major|minor)\] ([^:]+):")
_HEADING = re.compile(r"^\*\*([^*]+)\*\*")
_MORE = re.compile(r"^- _\(and \d+ more")


def view(text: str, kind: str) -> str:
    lines = text.strip().splitlines()
    if kind == "all":
        return text.strip()
    if kind == "questions":
        body = _blocks(lines, QUESTION_HEADINGS) or ["No reviewer questions were in the earlier review."]
    elif kind == "sections":
        body = [line for line in lines if line.startswith(MISSING)] or ["No sections were missing in the earlier review."]
    else:
        body = [line for group, line in _gap_lines(lines) if _keep(kind, group, line)]
        body = body or [f"No {kind} gaps were in the earlier review."]
        if any(_MORE.match(line) for line in lines):
            body.append(PARTIAL_NOTE)
    head = f"_Showing {kind} only, from the review earlier in our conversation._"
    return "\n".join([lines[0], head, "", *body])


def _gap_lines(lines: list[str]) -> list[tuple[str, str]]:
    found, group = [], ""
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            group = heading.group(1).strip()
        elif _GAP.match(line):
            found.append((group, line))
    return found


def _keep(kind: str, group: str, line: str) -> bool:
    severity, section = _GAP.match(line).groups()
    if kind == "blockers":
        return severity == "blocker"
    if kind == "metrics":
        return group == "Metrics" or section.strip() == "Metrics"
    return section.strip() == "Requirements"


def _blocks(lines: list[str], headings: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() not in headings:
            continue
        out += ([""] if out else []) + [line]
        for row in lines[index + 1:]:
            if not row.strip():
                break
            out.append(row)
    return out
