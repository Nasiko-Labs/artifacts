"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never data.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the
  last marker is used (the earlier table and result come from this agent's own checkpointer).
- File parts (raw bytes, either wire shape) are decoded when they are CSV/TSV/plain text up to 5 MB.
- JSON mode: "as json" / "json only" or metadata {"output": "json"}. PNG: "png" / "chart file" or
  metadata {"chart": "png"}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TABLE_TYPES = frozenset({"text/csv", "text/tab-separated-values", "text/plain", "application/csv",
                         "application/json"})
TABLE_SUFFIXES = {".csv": "text/csv", ".tsv": "text/tab-separated-values", ".txt": "text/plain",
                  ".json": "application/json"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_PNG_WORDS = re.compile(r"\bpng\b|\bchart file\b|\bimage file\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files."""

    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False
    wants_png: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw or "" if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    return read_parts(message.parts, metadata)


def read_parts(parts: Any, metadata: dict | None = None) -> Incoming:
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
    meta = metadata or {}
    wants_json = str(meta.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    wants_png = str(meta.get("chart") or "").casefold() == "png" or bool(_PNG_WORDS.search(user))
    return Incoming(text=user, context=tuple(t for t in texts[1:] if t.strip()), files=tuple(files),
                    notes=tuple(notes), wants_json=wants_json, wants_png=wants_png)


def media_type_of(name: str, media_type: str | None) -> str:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared in TABLE_TYPES:
        return declared
    return TABLE_SUFFIXES.get(PurePath(name).suffix.casefold(), declared or "unknown type")


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    """(text, None) for a readable table file; (None, note) for anything else."""
    kind = media_type_of(name, media_type)
    if data is None or kind not in TABLE_TYPES:
        return None, f"I can't read {name} ({kind}); send a CSV or paste the table instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it; paste a smaller extract."
    return data.decode("utf-8-sig", errors="replace"), None
