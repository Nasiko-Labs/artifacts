"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never text to scan.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier pastes and results come from this agent's own checkpointer).
- A text file part (raw bytes) is decoded and scanned too; other types are named in a note.
- Another agent's envelope (JSON with `text`, `notes` or `tldr`) is accepted as the source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_SOURCE_CHARS = 120_000
MIN_SOURCE_WORDS = 15
REQUEST_LINE_MAX_CHARS = 300
COLON_HEAD_MAX_CHARS = 160
ENVELOPE_FIELDS = ("text", "notes", "tldr")
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json",
                 ".eml": "text/plain"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_REQUEST = re.compile(r"\b(?:promis\w*|commitments?|what (?:did )?i owe|track my|scan|extract|what did i|"
                      r"here (?:are|is)|my (?:sent|notes|messages|emails?|chats?)|as of|today is|my name is|"
                      r"find (?:what|my|the)|list (?:my|what))\b", re.IGNORECASE)
_SPEAKS = re.compile(r"\b(?:I(?:['’]ll| will| can|['’]m going to| am going to)|let me|will (?:send|ping|check))\b")
_HEADERISH = re.compile(r"^\s*(?:from|to|cc|subject|date|sent)\s*:", re.IGNORECASE)
_MY_NAME = re.compile(r"(?:\b[Ii] am|\bI'm|\bI’m|\b[Mm]y name is|\b[Mm]e\s*=)\s+([A-Z][\w-]+)")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    instruction: str
    source: str


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    incoming = read_parts(message.parts)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    if str(metadata.get("output") or "").casefold() == "json":
        return Incoming(incoming.text, incoming.context, incoming.files, incoming.notes, True)
    return incoming


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
    user = current_message(texts[0]) if texts else ""
    return Incoming(text=user, context=tuple(t for t in texts[1:] if t.strip()), files=tuple(files),
                    notes=tuple(notes), wants_json=bool(_JSON_WORDS.search(user)))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its text instead."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
    """The source inside another agent's envelope, or None when `text` is not one."""
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
            return value.strip()
    return None


def split_request(text: str, files: tuple[tuple[str, str], ...] = ()) -> Request:
    """(instruction, source): a short first line that asks for the scan is the instruction, not source."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return Request("", enveloped)
    instruction, body = _request_line((text or "").strip())
    pieces = [body] if body.strip() else []
    pieces += [f"[File: {name}]\n{data.strip()}" for name, data in files if data.strip()]
    return Request(instruction, "\n\n".join(pieces))


def _request_line(text: str) -> tuple[str, str]:
    first, _, rest = text.partition("\n")
    head, colon, tail = first.partition(":")
    if _HEADERISH.match(first):
        return "", text
    if _SPEAKS.search(first) and not _REQUEST.search(first):
        return "", text
    if colon and len(head) <= COLON_HEAD_MAX_CHARS and _REQUEST.search(head) and (tail.strip() or rest.strip()):
        return head.strip(), f"{tail.strip()}\n{rest}".strip()
    if rest.strip() and len(first) <= REQUEST_LINE_MAX_CHARS and _REQUEST.search(first):
        return first.strip(), rest.strip()
    return "", text


def my_name(instruction: str) -> str:
    match = _MY_NAME.search(instruction or "")
    return match.group(1) if match else ""


def word_count(text: str) -> int:
    return len((text or "").split())


def head_of(source: str, limit: int = MAX_SOURCE_CHARS) -> str:
    if len(source) <= limit:
        return source
    cut = source.rfind("\n", limit // 2, limit)
    return source[:cut if cut != -1 else limit].rstrip()


def tail_of(source: str, limit: int = MAX_SOURCE_CHARS) -> str:
    if len(source) <= limit:
        return source
    start = len(source) - limit
    cut = source.find("\n", start, start + limit // 2)
    return source[cut + 1 if cut != -1 else start:].lstrip()
