"""Pure: what the user gave this turn, read from the A2A message parts.

- The user's own words are the FIRST text part. The platform appends org policy and agent knowledge as
  later, unmarked text parts: those are instructions for the model, never material to summarize.
- In a session with prior turns the text arrives as "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used (earlier inputs and results come from this agent's own checkpointer).
- File parts (raw bytes, from either wire shape) are decoded when they are a text type; other types are
  named in a note and skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_SOURCE_CHARS = 120_000
MIN_SOURCE_WORDS = 60
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 500

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json",
                        "text/vtt", "application/x-subrip", "text/srt", "application/srt"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv",
                 ".json": "application/json", ".vtt": "text/vtt", ".srt": "application/x-subrip"}
CUE_TYPES = frozenset({"text/vtt", "application/x-subrip", "text/srt", "application/srt"})

_CUE_TIMING = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->.*$", re.MULTILINE)
_CUE_NUMBER = re.compile(r"^\s*\d+\s*$", re.MULTILINE)
_VTT_HEADER = re.compile(r"\AWEBVTT[^\n]*\n", re.IGNORECASE)
_BLANK_RUNS = re.compile(r"\n{3,}")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Incoming:
    """One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files."""

    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = raw.rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_parts(parts: Any) -> Incoming:
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
                    notes=tuple(notes))


def media_type_of(name: str, media_type: str | None) -> str:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared in TEXT_TYPES:
        return declared
    return TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared or "unknown type")


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    """(text, None) for a readable text file; (None, note) for anything else."""
    kind = media_type_of(name, media_type)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind}) yet; paste its text instead."
    text = data.decode("utf-8", errors="replace")
    return (strip_cues(text) if kind in CUE_TYPES else text), None


def strip_cues(text: str) -> str:
    """Subtitle files: drop the header, cue numbers and timings, keep the spoken lines."""
    body = _VTT_HEADER.sub("", text.replace("\r\n", "\n"))
    body = _CUE_NUMBER.sub("", _CUE_TIMING.sub("", body))
    return _BLANK_RUNS.sub("\n\n", body).strip()


def split_request(text: str) -> tuple[str, str]:
    """(instruction, source). A short first paragraph before a long rest is the instruction."""
    stripped = (text or "").strip()
    match = _PARAGRAPH_BREAK.search(stripped)
    if match:
        head, rest = stripped[:match.start()].strip(), stripped[match.end():].strip()
        if len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS:
            return head, rest
    return "", stripped


def assemble_source(source: str, files: tuple[tuple[str, str], ...]) -> str:
    pieces = [source.strip()] if source.strip() else []
    pieces += [f"[File: {name}]\n{text.strip()}" for name, text in files if text.strip()]
    return "\n\n".join(pieces)


def word_count(text: str) -> int:
    return len((text or "").split())


def truncate_source(source: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """The first `limit` chars, cut back to the last paragraph or line break when there is one."""
    if len(source) <= limit:
        return source
    head = source[:limit]
    for boundary in ("\n\n", "\n", ". "):
        cut = head.rfind(boundary)
        if cut >= limit // 2:
            return head[:cut].rstrip()
    return head
