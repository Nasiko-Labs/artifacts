"""Pure: what the user gave this turn, read from the A2A message parts.

- The user's own words are the FIRST text part. The platform appends org policy and agent knowledge as
  later, unmarked text parts: those are instructions for the model, never part of the thread.
- In a session with prior turns the text arrives as "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used (the stored thread comes from this agent's own checkpointer).
- File parts (raw bytes) are decoded when they are a text type up to 5 MB; others are named in a note.
- A JSON envelope from a workflow step is accepted when its `text`, `thread` or `body` field is a string.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_SOURCE_CHARS = 120_000
MIN_SOURCE_WORDS = 60
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
ENVELOPE_FIELDS = ("text", "thread", "body")

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json", "message/rfc822"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
                 ".json": "application/json", ".eml": "text/plain"}

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*\n(?P<body>.*)\n```\s*\Z", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files."""

    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = raw.rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_parts(parts: Any, wants_json: bool = False) -> Incoming:
    """Splits message parts into the user's text, platform context and files (duck-typed a2a parts)."""
    texts: list[str] = []
    files: list[tuple[str, str]] = []
    notes: list[str] = []
    for part in parts or ():
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
    user = current_message(texts[0]) if texts else ""
    return Incoming(text=user, context=tuple(t for t in texts[1:] if t.strip()), files=tuple(files),
                    notes=tuple(notes), wants_json=wants_json)


def media_type_of(name: str, media_type: str | None) -> str:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared in TEXT_TYPES:
        return declared
    return TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared or "unknown type")


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    """(text, None) for a readable text file; (None, note) for anything else."""
    kind = media_type_of(name, media_type)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind}); paste the thread text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it; paste the part of the thread you need."
    return data.decode("utf-8", errors="replace"), None


def split_request(text: str) -> tuple[str, str]:
    """(instruction, thread). A short first paragraph before a long rest is the instruction."""
    stripped = (text or "").strip()
    match = _PARAGRAPH_BREAK.search(stripped)
    if match:
        head, rest = stripped[:match.start()].strip(), stripped[match.end():].strip()
        if len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS:
            return head, rest
    return "", stripped


def envelope_thread(text: str) -> str | None:
    """None: not JSON, read the text as is. "": a JSON object with no thread field (rejected).
    Otherwise the thread text held in the envelope's `text`, `thread` or `body` field."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group("body") if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, Mapping):
        return None
    for key in ENVELOPE_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def assemble_source(source: str, files: tuple[tuple[str, str], ...]) -> str:
    pieces = [source.strip()] if source.strip() else []
    pieces += [text.strip() for _name, text in files if text.strip()]
    return "\n\n".join(pieces)


def word_count(text: str) -> int:
    return len((text or "").split())


def head_part(source: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """The first `limit` chars, cut back to the last line break when there is one."""
    if len(source) <= limit:
        return source
    head = source[:limit]
    cut = head.rfind("\n")
    return head[:cut].rstrip() if cut >= limit // 2 else head


def tail_part(source: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """The last `limit` chars, starting at a line start when there is one."""
    if len(source) <= limit:
        return source
    tail = source[-limit:]
    cut = tail.find("\n")
    return tail[cut + 1:].lstrip() if 0 <= cut <= limit // 2 else tail
