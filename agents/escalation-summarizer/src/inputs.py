"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions that shape wording only, never thread content.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the
  last marker is used (the earlier thread and result come from this agent's own checkpointer).
- File parts (raw bytes) are decoded when they are a text type; other types are named in a note.
- Another agent's envelope (JSON with `summary`, `items`, `text`, `ask`...) is read as thread content,
  each line labelled as coming from a previous step.
- "as json" / "json only" or message metadata {"output": "json"} asks for the envelope as the text part.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

from messages import is_header

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_THREAD_CHARS = 120_000
MIN_THREAD_WORDS = 80
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 300
INSTRUCTION_MAX_WORDS = 12
ENVELOPE_TEXT_FIELDS = ("summary", "text", "ask", "customer_impact", "body", "description")
FROM_STEP = "From a previous step"

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json", "text/vtt"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv",
                 ".json": "application/json", ".vtt": "text/vtt", ".eml": "text/plain", ".log": "text/plain"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_INSTRUCTION_LINE = re.compile(r"^(?:please\s+)?(?:summari[sz]e|escalat|tl;?dr|recap|for (?:the|our|my|a|an)\b|"
                               r"tone:|max \d)", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input. `context` = org policy / knowledge parts; `files` = decoded (name, text)."""

    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    incoming = read_parts(message.parts)
    if str(metadata.get("output") or "").casefold() == "json":
        return Incoming(incoming.text, incoming.context, incoming.files, incoming.notes, True)
    return incoming


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


def without_json_words(text: str) -> str:
    return " ".join(_JSON_WORDS.sub(" ", text or "").split()) if _JSON_WORDS.search(text or "") else (text or "")


def envelope_as_thread(text: str) -> str:
    """A pasted envelope (fenced or the whole message) becomes labelled thread lines; else unchanged."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.search(stripped)
    body, rest = (fenced.group(1), stripped[:fenced.start()] + stripped[fenced.end():]) if fenced else (stripped, "")
    data = _as_envelope(body) if body.startswith("{") else None
    if data is None:
        return text
    return "\n\n".join(p for p in (rest.strip(), "\n".join(_envelope_lines(data))) if p)


def _as_envelope(body: str) -> dict | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and any(k in data for k in (*ENVELOPE_TEXT_FIELDS, "items")):
        return data
    return None


def _envelope_lines(data: dict) -> list[str]:
    lines = [f"{FROM_STEP} ({key}): {' '.join(data[key].split())}" for key in ENVELOPE_TEXT_FIELDS
             if isinstance(data.get(key), str) and data[key].strip()]
    items = data.get("items") if isinstance(data.get("items"), list) else []
    for item in items:
        text = item.get("text") or item.get("summary") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            lines.append(f"{FROM_STEP} (item): {' '.join(text.split())}")
    return lines


def split_request(text: str) -> tuple[str, str]:
    """(instruction, thread). A short first paragraph (or an instruction-like first line) before a long
    rest is the instruction."""
    stripped = (text or "").strip()
    match = _PARAGRAPH_BREAK.search(stripped)
    if match:
        head, rest = stripped[:match.start()].strip(), stripped[match.end():].strip()
        if len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS and _looks_like_instruction(head):
            return head, rest
    head, _, rest = stripped.partition("\n")
    if len(head) <= INSTRUCTION_MAX_CHARS and len(rest) >= SPLIT_MIN_REST_CHARS and _INSTRUCTION_LINE.match(head):
        return head.strip(), rest.strip()
    return "", stripped


def _looks_like_instruction(head: str) -> bool:
    if "\n" in head or is_header(head):
        return False
    return bool(_INSTRUCTION_LINE.match(head)) or word_count(head) <= INSTRUCTION_MAX_WORDS


def assemble(thread: str, files: tuple[tuple[str, str], ...]) -> str:
    pieces = [thread.strip()] if thread.strip() else []
    pieces += [f"[File: {name}]\n{text.strip()}" for name, text in files if text.strip()]
    return "\n\n".join(pieces)


def word_count(text: str) -> int:
    return len((text or "").split())


def keep_tail(thread: str, limit: int = MAX_THREAD_CHARS) -> str:
    """The most recent `limit` chars, starting at a line start when one is in reach."""
    if len(thread) <= limit:
        return thread
    tail = thread[-limit:]
    line = tail.find("\n")
    return tail[line + 1:] if 0 <= line < limit // 2 else tail
