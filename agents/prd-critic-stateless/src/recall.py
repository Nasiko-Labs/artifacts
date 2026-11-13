"""Pure: the earlier PRD and review, re-read from the conversation history (this agent keeps no state).

- The PRD: the newest user message whose PRD part has 150+ words (cut to the character cap when longer), with
  its request line.
- The review, from the newest assistant reply newer than that PRD: either its JSON envelope (an "as json"
  reply: every follow-up works from it with no call) or the rendered markdown review ("**PRD review: N/100"),
  kept as text so views can be cut from it (`replay.py`).
Either may be missing (history trimmed, compressed or absent); callers then review again or ask for a re-paste.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
from history import ASSISTANT, USER, Turn
from inputs import split_request, word_count

MIN_PRD_WORDS = 150
MAX_PRD_CHARS = 60_000
REVIEW_TITLE = "**PRD review: "

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_COUNTS = re.compile(r"(\d+) blockers?, (\d+) major, (\d+) minor")
_ENVELOPE_KEYS = ("gaps", "score", "band", "deductions", "questions", "prd_questions", "summary")


@dataclass(frozen=True)
class Prd:
    text: str
    instruction: str = ""


@dataclass(frozen=True)
class Prior:
    prd: Prd | None = None
    envelope: dict | None = None      # a full review envelope (from an "as json" reply)
    review_text: str = ""             # a rendered, unfocused review


def read(turns: Sequence[Turn]) -> Prior:
    prd_at, prd = -1, None
    for index in range(len(turns) - 1, -1, -1):
        if turns[index].role == USER:
            prd = from_paste(turns[index].text)
            if prd is not None:
                prd_at = index
                break
    for turn in reversed(turns[prd_at + 1:]):
        if turn.role != ASSISTANT:
            continue
        envelope = from_json(turn.text)
        if envelope is not None:
            return Prior(prd, envelope)
        if is_full_review(turn.text):
            return Prior(prd, None, turn.text)
    return Prior(prd)


def from_paste(text: str) -> Prd | None:
    request = split_request(text)
    body = prompts.strip_markers(request.prd)
    if word_count(body) < MIN_PRD_WORDS:
        return None
    return Prd(cut(body)[0], request.instruction)


def cut(body: str) -> tuple[str, int]:
    """(the PRD within the character cap, cut at a line break when there is one; lines left out)."""
    if len(body) <= MAX_PRD_CHARS:
        return body, 0
    head = body[:MAX_PRD_CHARS]
    end = head.rfind("\n")
    kept = head[:end] if end > 0 else head
    return kept, len([line for line in body[len(kept):].splitlines() if line.strip()])


def from_json(text: str) -> dict | None:
    fenced = _FENCED_JSON.search(text or "")
    if fenced is None:
        return None
    try:
        data = json.loads(fenced.group(1))
    except ValueError:
        return None
    if not isinstance(data, dict) or any(k not in data for k in _ENVELOPE_KEYS) or not isinstance(data["gaps"], list):
        return None
    counts = _COUNTS.search(str(data["summary"]))
    if counts is None or sum(int(n) for n in counts.groups()) != len(data["gaps"]):  # a narrowed view
        return None
    return data


def is_full_review(text: str) -> bool:
    """A rendered review that is not a focused view ("_Showing blockers only ...")."""
    return (text or "").lstrip().startswith(REVIEW_TITLE) and "_Showing " not in text
