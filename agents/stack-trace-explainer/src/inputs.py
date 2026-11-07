"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; with chat history it is "{history}\\n\\nCurrent message: {text}"
  and only the text after the last marker is used (earlier traces come from this agent's checkpointer).
- Later text parts are org policy / agent knowledge: system context for the model, never the request or evidence.
- A text file part (inline bytes, <= 5 MB) is treated as pasted text: a trace stays a trace, anything else becomes a
  code block labelled `# file: <name>`.
- Another step's envelope (JSON with `items[].trace` or `summary`) is read as the trace text.
- Pasted data is fenced for the model; the fence markers are stripped from the data first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_MESSAGE_CHARS = 100_000
MAX_FILE_BYTES = 5 * 1024 * 1024
FENCE_OPEN, FENCE_CLOSE = "<<<INPUT>>>", "<<<END>>>"
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json", "text/csv",
                        "text/x-diff", "text/x-patch"})
TEXT_SUFFIXES = (".txt", ".log", ".md", ".json", ".csv", ".diff", ".patch", ".py", ".js", ".ts", ".java", ".kt",
                 ".go", ".rs", ".rb", ".cs")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_MARKERS = re.compile(r"<<<\s*/?\s*[A-Za-z_]+\s*>>>")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_TRACE_START = re.compile(r"Traceback \(most recent call last\)|^\s*at \S|panicked at|^panic: |Exception in thread",
                          re.MULTILINE)


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[str] = []
    notes: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            text, note = decode_file(part.filename or "attached file", part.media_type,
                                     part.raw if kind == "raw" else None)
            if text is not None:
                files.append(text)
            if note:
                notes.append(note)
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    combined = "\n\n".join(t for t in (user, *files) if t.strip())
    if len(combined) > MAX_MESSAGE_CHARS:
        notes.append(f"Your message was over {MAX_MESSAGE_CHARS:,} characters; I read the last "
                     f"{MAX_MESSAGE_CHARS:,} (logs end with the fatal error).")
        combined = combined[-MAX_MESSAGE_CHARS:]
    return Incoming(combined, tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    readable = declared in TEXT_TYPES or PurePath(name).suffix.casefold() in TEXT_SUFFIXES
    if data is None or not readable:
        return None, f"I can't read {name} ({declared or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it; paste the error and the lines around it instead."
    text = data.decode("utf-8", errors="replace")
    if _TRACE_START.search(text):
        return text, None
    return f"# file: {name}\n{text}", None


def strip_markers(text: str) -> str:
    return _MARKERS.sub("", text or "")


def fence(text: str, name: str = "INPUT") -> str:
    """Untrusted data between markers the data itself can no longer contain."""
    return f"<<<{name}>>>\n{strip_markers(text)}\n<<<END>>>"


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def from_envelope(text: str) -> str | None:
    """The trace text inside a previous step's envelope (`items[].trace`, else `summary`), or None."""
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
    traces = [str(item["trace"]) for item in data.get("items") or () if isinstance(item, dict)
              and isinstance(item.get("trace"), str) and item["trace"].strip()]
    if traces:
        return "\n\n".join(traces)
    summary = data.get("summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else None
