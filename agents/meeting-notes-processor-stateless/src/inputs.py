"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never meeting content.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}". The text after the last
  marker is this turn's message; the history before it is parsed into turns (`history.py`) and is this
  agent's only memory: it keeps nothing between messages.
- File parts (inline bytes) are decoded when they are text; VTT / SRT keep their cue timings, which the
  transcript parser turns into turn times.
- Another agent's envelope (JSON with `transcript` or `text`) is accepted as the transcript.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

import history
from history import HistoryTurn

CURRENT_MESSAGE_MARKER = history.CURRENT_MESSAGE_MARKER
MAX_SOURCE_CHARS = 120_000
MIN_SOURCE_WORDS = 80
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 500
MAX_FILE_BYTES = 5 * 1024 * 1024
ENVELOPE_FIELDS = ("transcript", "text")

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/vtt", "application/x-subrip",
                        "text/srt", "application/srt"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".vtt": "text/vtt",
                 ".srt": "application/x-subrip"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s")
_REQUEST_LINE = re.compile(r"\b(?:transcript|notes|minutes|meeting|sync|call|stand-?up|process|i'?m|my name is|"
                           r"as json)\b", re.IGNORECASE)
_SPEAKER_LINE = re.compile(r"^\s*(?:[\[(]?\d{1,2}:\d{2}|[A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3}\s*(?:[\[(]\d|:\s))")
_MY_NAME = re.compile(r"\b(?i:i'?m|i am|my name is)\s+([A-Z][a-z'’-]+(?: [A-Z][a-z'’-]+)?)")
_TITLE = re.compile(r"\b(?:notes|minutes|transcript)\s+(?:from|of|for)\s+(?:(?:the|our|this|my|today'?s)\s+)?"
                    r"([^,.:;()\n]{3,60})", re.IGNORECASE)
_NAME_ONLY = re.compile(r"^\W*(?i:i'?m|i am|my name is)\s+([A-Z][a-z'’-]+(?: [A-Z][a-z'’-]+)?)\W*$")


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[HistoryTurn, ...] = ()


def current_message(raw: str) -> str:
    return history.split(raw)[1]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    incoming = read_parts(message.parts)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or incoming.wants_json
    return Incoming(incoming.text, incoming.context, incoming.files, incoming.notes, wants, incoming.history)


def read_parts(parts: Any) -> Incoming:
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
            files += [(name, decoded)] if decoded is not None else []
            notes += [note] if note else []
    block, user = history.split(texts[0]) if texts else ("", "")
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), tuple(notes),
                    bool(_JSON_WORDS.search(user)), history.parse(block))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; paste the part you need instead."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
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
    return next((data[f].strip() for f in ENVELOPE_FIELDS if isinstance(data.get(f), str) and data[f].strip()), None)


def split_request(text: str) -> tuple[str, str]:
    """(instruction, transcript). A short first paragraph before a long rest is the instruction; so is a
    short first line that asks for the notes and is not itself a speaker line."""
    stripped = (text or "").strip()
    match = _PARAGRAPH_BREAK.search(stripped)
    if match:
        head, rest = stripped[:match.start()].strip(), stripped[match.end():].strip()
        if (len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS and "\n" not in head
                and not _SPEAKER_LINE.match(head) and not _BULLET.match(head)):
            return head, rest
    first, _, rest = stripped.partition("\n")
    if (len(first) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS and _REQUEST_LINE.search(first)
            and not _SPEAKER_LINE.match(first)):
        return first.strip(), rest.strip()
    return "", stripped


def my_name_in(instruction: str) -> str:
    match = _MY_NAME.search(instruction or "")
    return match.group(1).strip() if match else ""


def title_in(instruction: str) -> str:
    """A meeting title named in the request ("Notes from the Tuesday product sync"), else ""."""
    match = _TITLE.search(instruction or "")
    title = match.group(1).strip() if match else ""
    return title[:1].upper() + title[1:]


def name_only(text: str) -> str:
    """The name in a message that only says who the user is ("I'm Raj"), else ""."""
    match = _NAME_ONLY.match(text or "")
    return match.group(1).strip() if match else ""


def word_count(text: str) -> int:
    return len((text or "").split())


def cut(source: str, from_end: bool, limit: int = MAX_SOURCE_CHARS) -> str:
    """The first (or last) `limit` chars, cut back to a line break when there is one in reach."""
    if len(source) <= limit:
        return source
    piece = source[-limit:] if from_end else source[:limit]
    at = piece.find("\n") if from_end else piece.rfind("\n")
    if from_end:
        return piece[at + 1:] if 0 <= at < limit // 2 else piece
    return piece[:at] if at >= limit // 2 else piece
