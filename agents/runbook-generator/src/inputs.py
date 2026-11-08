"""Pure: what the user gave this turn, read from the A2A message, and how it splits into request and source.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never source text.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier sources and runbooks come from this agent's own checkpointer).
- File parts (raw bytes, either wire shape) are decoded when they are a text type; others get a note.
- Another agent's envelope (JSON with `postmortem_markdown`, `timeline` or `summary`) is read as a transcript.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_SOURCE_CHARS = 100_000
MIN_SOURCE_WORDS = 30
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 500
ENVELOPE_FIELDS = ("postmortem_markdown", "timeline", "summary")

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json", "text/vtt",
                        "application/x-subrip", "text/srt", "application/srt"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".log": "text/plain",
                 ".json": "application/json", ".vtt": "text/vtt", ".srt": "application/x-subrip"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, policy parts, decoded files, notes, and whether JSON was asked."""

    text: str
    policy: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    source: str
    instruction: str = ""
    from_envelope: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; text files are decoded."""
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
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    policy = tuple(t for t in texts[1:] if t.strip())
    return Incoming(user, policy, tuple(files), tuple(notes), wants)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    """(text, None) for a readable text file; (None, note) for anything else."""
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its text instead."
    return data.decode("utf-8", errors="replace"), None


def word_count(text: str) -> int:
    return len((text or "").split())


def from_envelope(text: str) -> str | None:
    """The transcript inside another agent's envelope, or None when `text` is not one."""
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
    pieces = [_field_text(data.get(name)) for name in ENVELOPE_FIELDS]
    joined = "\n\n".join(p for p in pieces if p)
    return joined or None


def _field_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        lines = [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value]
        return "\n".join(line.strip() for line in lines if line and line.strip())
    return ""


def split_request(text: str, files: tuple[tuple[str, str], ...] = ()) -> Request:
    """(source, instruction). A short first paragraph before a long rest is the instruction; text files are
    appended to the source; an envelope is read as a transcript."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return Request(enveloped, from_envelope=True)
    stripped = (text or "").strip()
    instruction, source = "", stripped
    match = _PARAGRAPH_BREAK.search(stripped)
    if match:
        head, rest = stripped[:match.start()].strip(), stripped[match.end():].strip()
        if len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS:
            instruction, source = head, rest
    pieces = [source] if source else []
    pieces += [f"[File: {name}]\n{body.strip()}" for name, body in files if body.strip()]
    return Request("\n\n".join(pieces), instruction)
