"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts (org policy, agent knowledge) are platform
  instructions, never incident material.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is read (earlier logs and results come from this agent's checkpointer).
- File parts (inline bytes, either wire shape) are decoded when they are text; a chat export
  (`[{user, text, ts}]`) is flattened to `<UTC time> name: text` lines in code.
- Another step's envelope (JSON with `summary` / `items`) is accepted as the log.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_SOURCE_CHARS = 120_000
MIN_LOG_WORDS = 80
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 500
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json"})
TEXT_SUFFIXES = {".txt": "text/plain", ".log": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
                 ".csv": "text/csv", ".json": "application/json"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_REQUEST_LINE = re.compile(r"post-?mortem|incident review|retro(?:spective)?|\bsev\s?-?\d\b|\bwrite\b|\bdraft\b",
                           re.IGNORECASE)
_HAS_TIME = re.compile(r"\d{1,2}:\d{2}")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_SEVERITY = re.compile(r"\b(sev\s?-?[0-5]|p[0-4])\b", re.IGNORECASE)
_TITLE_LABEL = re.compile(r"\btitle\s*[:=]\s*(?P<t>[^,\n]{3,100})", re.IGNORECASE)
_TITLE_FOR = re.compile(r"post-?mortem (?:for|of|on|about) (?:the |our )?(?P<t>[^,:\n]{3,80})", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """First text part = the user's words; later text parts = platform context; files decoded."""
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
            files += [(name, decoded)] if decoded is not None else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), tuple(notes), wants)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its text instead."
    text = data.decode("utf-8", errors="replace")
    return (flatten_export(text) or text), None


def flatten_export(text: str) -> str | None:
    """A chat export `[{user, text, ts}, ...]` as `<ISO UTC> user: text` lines, or None if it isn't one."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list) or not data or not all(isinstance(m, Mapping) and "text" in m for m in data):
        return None
    lines = []
    for message in data:
        stamp = _utc(message.get("ts"))
        who = str(message.get("user_name") or message.get("user") or "").strip()
        body = " ".join(str(message.get("text") or "").split())
        lines.append(" ".join(p for p in (stamp, f"{who}:" if who else "", body) if p))
    return "\n".join(lines)


def _utc(ts: Any) -> str:
    try:
        moment = datetime.fromtimestamp(float(ts), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def from_envelope(text: str) -> str | None:
    """The log inside another step's envelope (`summary` + `items`), or None when `text` isn't one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, Mapping) or not ({"summary", "items"} & data.keys()):
        return None
    lines = [str(data.get("summary") or "").strip()]
    for item in data.get("items") or ():
        if isinstance(item, Mapping):
            lines.append(" ".join(str(v) for v in item.values() if isinstance(v, (str, int, float)) and str(v)))
        elif isinstance(item, str):
            lines.append(item)
    return "\n".join(line for line in lines if line.strip()) or None


def split_request(text: str) -> tuple[str, str]:
    """(instruction, log). A short first paragraph before a long rest, or a first line that asks for a
    postmortem and carries no timestamp, is the instruction."""
    stripped = (text or "").strip()
    enveloped = from_envelope(stripped)
    if enveloped is not None:
        return "", enveloped
    match = _PARAGRAPH_BREAK.search(stripped)
    if match:
        head, rest = stripped[:match.start()].strip(), stripped[match.end():].strip()
        if len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS:
            return head, rest
    first, _, rest = stripped.partition("\n")
    if rest.strip() and len(first) <= INSTRUCTION_MAX_CHARS and _REQUEST_LINE.search(first) \
            and not _HAS_TIME.search(first):
        return first.strip(), rest.strip()
    return "", stripped


def assemble(log: str, files: tuple[tuple[str, str], ...]) -> str:
    pieces = [log.strip()] if log.strip() else []
    pieces += [text.strip() for _, text in files if text.strip()]
    return "\n\n".join(pieces)


def severity_of(instruction: str) -> str:
    match = _SEVERITY.search(instruction or "")
    return match.group(1) if match else ""


def title_of(instruction: str) -> str:
    for pattern in (_TITLE_LABEL, _TITLE_FOR):
        match = pattern.search(instruction or "")
        if match:
            return _SEVERITY.sub("", match["t"]).strip(" .,-")
    return ""


def word_count(text: str) -> int:
    return len((text or "").split())
