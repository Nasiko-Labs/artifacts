"""Pure: what the user gave this turn, read from the A2A message parts (where the draft is: `natural.py`).

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge: style
  instructions for the model, never part of the draft and never grounding.
- In a session with prior turns the text arrives as "{history}\\n\\nCurrent message: {text}". The text after
  the last marker is this turn's message; the history before it is parsed into turns (`history.py`) and is
  this agent's only memory: it keeps nothing between messages.
- A text file part (an API client may send one) is the draft; other file types are named in a note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import history
from history import HistoryTurn

CURRENT_MESSAGE_MARKER = history.CURRENT_MESSAGE_MARKER
MIN_DRAFT_WORDS = 15
MAX_DRAFT_CHARS = 20_000
FIRST_PART_WORDS = 3_000

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".json": "application/json"}

_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    history: tuple[HistoryTurn, ...] = ()


def current_message(raw: str) -> str:
    return history.split(raw)[1]


def read_parts(parts: Any) -> Incoming:
    """Splits message parts into the user's text, platform context and text files (duck-typed a2a parts)."""
    texts: list[str] = []
    files: list[tuple[str, str]] = []
    notes: list[str] = []
    for part in parts or ():
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "the attached file"
            media = _media_type(name, part.media_type)
            if kind == "raw" and media in TEXT_TYPES:
                files.append((name, part.raw.decode("utf-8", errors="replace")))
            else:
                notes.append(f"I can't read {name} ({media}); paste its text instead.")
    block, user = history.split(texts[0]) if texts else ("", "")
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), tuple(notes), history.parse(block))


def _media_type(name: str, media_type: str | None) -> str:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared in TEXT_TYPES:
        return declared
    return TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared or "unknown type")


def word_count(text: str) -> int:
    return len((text or "").split())


def first_part(draft: str, words: int = FIRST_PART_WORDS, limit: int = MAX_DRAFT_CHARS) -> str:
    """About the first `words` words, cut back to a paragraph or line break, never over `limit` chars."""
    ends = [m.end() for m in _WORD.finditer(draft)]
    if len(ends) <= words and len(draft) <= limit:
        return draft
    head = draft[:min(ends[min(words, len(ends)) - 1], limit)]
    for boundary in ("\n\n", "\n", ". "):
        at = head.rfind(boundary)
        if at >= len(head) // 2:
            return head[:at + (1 if boundary == ". " else 0)].rstrip()
    return head.rstrip()
