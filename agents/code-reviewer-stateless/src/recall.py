"""Pure: what earlier turns left behind, recovered from the platform's chat history (no checkpointer).

- `source`: the most recent user message that held a diff (with its spec / standards / focus), cut to the cap.
- `review`: our most recent rendered review (`**Review: ...`), so "explain comment 3" means the comment 3
  the user saw.
- `asked_for`: when our last reply asked for the diff, the request that prompted it (its spec / standards).
Any field may be empty: the history is budgeted and may have been trimmed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
from diff_parse import parse
from history import ASSISTANT, USER, Turn
from inputs import MAX_DIFF_CHARS, from_envelope

REVIEW_MARK = "**Review: "
MAX_KEPT_CHARS = 15_000
_FILE = re.compile(r"^\*\*(?P<file>[^*\n]+)\*\*$")
_COMMENT = re.compile(r"^- \*\*(?P<n>\d{1,2})\. (?P<severity>[A-Za-z]+) · (?P<axis>[a-z_]+) · "
                      r"L(?P<line>\d+)(?:-(?P<end>\d+))?\*\* (?P<text>.+)$")


@dataclass(frozen=True)
class Prior:
    source: str = ""
    review: str = ""
    asked_for: str = ""

    @property
    def has_result(self) -> bool:
        return bool(self.source or self.review)


def recall(turns: Sequence[Turn]) -> Prior:
    return Prior(source=_last_source(turns), review=_last_review(turns), asked_for=_asked_for(turns))


def _last_source(turns: Sequence[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == USER:
            source = (from_envelope(turn.text) or turn.text)[:MAX_DIFF_CHARS]
            if parse(source).files:
                return source
    return ""


def _last_review(turns: Sequence[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == ASSISTANT and turn.text.startswith(REVIEW_MARK):
            return turn.text
    return ""


def _asked_for(turns: Sequence[Turn]) -> str:
    if len(turns) < 2 or turns[-1].role != ASSISTANT or not turns[-1].text.startswith(prompts.NO_DIFF_LEAD[:30]):
        return ""
    before = turns[-2]
    return before.text[:MAX_KEPT_CHARS] if before.role == USER else ""


def comment_from_review(review: str, number: int) -> dict | None:
    """Comment `number` as the rendered review showed it (file, lines, severity, axis, text), or None."""
    current = ""
    for line in (review or "").splitlines():
        header = _FILE.match(line.strip())
        if header and not line.startswith(REVIEW_MARK):
            current = header.group("file")
            continue
        found = _COMMENT.match(line.strip())
        if found and int(found.group("n")) == number and current:
            end = int(found.group("end")) if found.group("end") else None
            return {"file": current, "line": int(found.group("line")), "end_line": end, "quote": "",
                    "severity": found.group("severity").casefold(), "axis": found.group("axis"),
                    "comment": found.group("text"), "suggestion": None}
    return None
