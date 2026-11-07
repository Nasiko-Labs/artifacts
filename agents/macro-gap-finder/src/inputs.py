"""Pure: what the user gave this turn, read from the A2A message parts.

- The user's own words are the FIRST text part. The platform appends org policy and agent knowledge as
  later, unmarked text parts: those are instructions for the model, never replies to analyse.
- In a session with prior turns the text arrives as "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used (earlier replies and results come from this agent's own checkpointer).
- File parts (raw bytes, CSV/JSON/text, <= 5 MB) are decoded and added to the paste; others get a note.
- "as json" / "json only" in the text, or message metadata {"output": "json"}, asks for a JSON reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json", "text/tab-separated-values"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json",
                 ".tsv": "text/tab-separated-values"}

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files."""

    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False

    @property
    def paste(self) -> str:
        """The user's text plus any decoded file text (files after the text)."""
        pieces = [self.text.strip()] if self.text.strip() else []
        pieces += [text.strip() for _, text in self.files if text.strip()]
        return "\n\n".join(pieces)


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """Reads one A2A message (duck-typed proto)."""
    if message is None:
        return Incoming("")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    return read_parts(message.parts, metadata)


def read_parts(parts: Any, metadata: dict | None = None) -> Incoming:
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
    wants = str((metadata or {}).get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(text=user, context=tuple(t for t in texts[1:] if t.strip()), files=tuple(files),
                    notes=tuple(notes), wants_json=wants)


def media_type_of(name: str, media_type: str | None) -> str:
    declared = (media_type or "").split(";")[0].strip().casefold()
    if declared in TEXT_TYPES:
        return declared
    return TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared or "unknown type")


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    """(text, None) for a readable text file; (None, note) for anything else."""
    kind = media_type_of(name, media_type)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind}); paste its replies as text or CSV instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it; paste a narrower export."
    return data.decode("utf-8-sig", errors="replace"), None
