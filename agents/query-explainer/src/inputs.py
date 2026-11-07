"""Pure: what the user gave this turn, read from the A2A message parts.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never the request and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the stored SQL and result come from this agent's own checkpointer).
- A FilePart (inline bytes of a text type, <= 5 MB) is read as pasted text, labelled `-- file: <name>`.
- Another step's envelope (JSON with `items[].sql` or `summary`) is accepted as the SQL text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json",
                        "text/x-diff", "text/x-patch", "application/sql", "text/x-sql"})
TEXT_SUFFIXES = (".sql", ".txt", ".md", ".json", ".csv", ".diff", ".patch")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|<<<\s*END\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words (plus any readable file), policy parts, JSON wish, file notes."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; files are appended to the user's text."""
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
            text, note = _file_text(part.filename or "attached file", part.media_type,
                                    part.raw if kind == "raw" else None)
            files += [text] if text else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    if files:
        user = "\n\n".join([user, *files]) if user.strip() else "\n\n".join(files)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def _file_text(name: str, media_type: str | None, data: bytes | None) -> tuple[str, str]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    readable = declared in TEXT_TYPES or PurePath(name).suffix.casefold() in TEXT_SUFFIXES
    if data is None or not readable:
        return "", f"I can't read {name} ({declared or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return "", f"{name} is over 5 MB, so I skipped it; paste the statements you care about."
    label = re.sub(r"[\r\n]+", " ", name)
    return f"-- file: {label}\n{data.decode('utf-8', errors='replace')}", ""


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def from_envelope(text: str) -> str | None:
    """The SQL inside another step's envelope (`items[].sql`, else `summary`), or None when not an envelope."""
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
    items = data.get("items") if isinstance(data.get("items"), list) else []
    sqls = [i["sql"].strip() for i in items if isinstance(i, dict) and isinstance(i.get("sql"), str) and i["sql"].strip()]
    if sqls:
        return "\n;\n".join(sqls)
    summary = data.get("summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


def strip_markers(text: str) -> str:
    """Removes anything shaped like a fence marker, so the input cannot close a fence."""
    return _MARKER.sub("", text or "")


def fence_text(body: str, name: str = "INPUT") -> str:
    """Untrusted data between markers; the markers are stripped from the body first."""
    return f"<<<{name}>>>\n{strip_markers(body)}\n<<<END>>>"
