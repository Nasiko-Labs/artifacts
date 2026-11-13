"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  style instructions for the model, never notes.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; the text after the last
  marker is this turn's message and the history before it is parsed into turns (`history.py`): the only
  memory of earlier turns (nothing is checkpointed).
- Text file parts (API clients only) are appended to the notes; other types are named in a note.
- Another agent's envelope (JSON with `notes`, `text`, `handoff_markdown` or `summary`) is accepted as notes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

from history import Turn, split
from history import parse as parse_history

MAX_NOTES_CHARS = 100_000
MIN_NOTES_WORDS = 40
ENVELOPE_FIELDS = ("notes", "text", "handoff_markdown", "summary")
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv",
                 ".json": "application/json", ".log": "text/plain"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, policy parts, decoded text files, whether JSON was asked."""

    text: str
    policy: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[Turn, ...] = ()
    had_history: bool = False       # a history block was sent, even one too garbled to parse


def current_message(raw: str) -> str:
    return split(raw)[1]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[tuple[str, str]] = []
    notes: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "the attached file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            if decoded is not None:
                files.append((name, decoded))
            if note:
                notes.append(note)
    block, user = split(texts[0]) if texts else ("", "")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), tuple(notes), wants,
                    parse_history(block), bool(block.strip()))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its text instead."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
    """The notes inside another agent's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for name in ENVELOPE_FIELDS:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def assemble(text: str, files: tuple[tuple[str, str], ...]) -> str:
    pieces = [text.strip()] if text.strip() else []
    pieces += [text_.strip() for _, text_ in files if text_.strip()]
    return "\n\n".join(pieces)


def word_count(text: str) -> int:
    return len((text or "").split())


def most_recent(text: str, limit: int = MAX_NOTES_CHARS) -> str:
    """The last `limit` chars, cut forward to the next line break so no entry starts mid-line."""
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    cut = tail.find("\n")
    return tail[cut + 1:] if 0 <= cut < limit // 2 else tail


def enough_notes(instruction: str, notes: str) -> bool:
    """Enough to build a handoff: the request words count too, since a one-line request ("Write my shift
    handoff from these notes: ...") is split at the colon and its notes alone may fall short."""
    return bool(notes.strip()) and word_count(f"{instruction} {notes}") >= MIN_NOTES_WORDS
