"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never the report or the export.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the earlier report, export and result come from this agent's own checkpointer).
- A text file part (an API client may send one) is appended under a label: CSV/TSV/JSON as `Tickets:`,
  anything else readable as `Report:`. Other file types are named in a note and skipped.
- "as json" / "json only" in the message, or message metadata {"output": "json"}, asks for the envelope as text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
EXPORT_TYPES = frozenset({"text/csv", "text/tab-separated-values", "application/json"})
REPORT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
SUFFIXES = {".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json",
            ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words (files appended), platform policy parts, JSON output wanted."""

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
            block, note = _file_block(part.filename or "the attached file", part.media_type,
                                      part.raw if kind == "raw" else None)
            files += [block] if block else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    text = "\n\n".join(p for p in (user, *files) if p.strip())
    return Incoming(text, tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def word_count(text: str) -> int:
    return len((text or "").split())


def _file_block(name: str, media_type: str | None, data: bytes | None) -> tuple[str, str]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in EXPORT_TYPES | REPORT_TYPES else SUFFIXES.get(PurePath(name).suffix.casefold(), "")
    if data is None or not kind or len(data) > MAX_FILE_BYTES:
        return "", f"I can't read {name} ({declared or 'unknown type'}); paste its text instead."
    label = "Tickets:" if kind in EXPORT_TYPES else "Report:"
    return f"{label}\n{data.decode('utf-8', errors='replace').strip()}", ""
