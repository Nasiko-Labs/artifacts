"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. The platform appends org policy and agent knowledge as
  later, unmarked text parts: those are instructions for the model, never sources.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the
  last marker is used (earlier sources and results come from this agent's own checkpointer).
- File parts (raw bytes) of a text type become sources named by their filename; others get a note.
- "as json" / "json only" in the text, or metadata {"output": "json"}, asks for the envelope as JSON.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024

TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json",
                        "text/vtt", "application/x-subrip", "text/srt", "application/srt"})
TEXT_SUFFIXES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv",
                 ".json": "application/json", ".vtt": "text/vtt", ".srt": "application/x-subrip"}
CUE_TYPES = frozenset({"text/vtt", "application/x-subrip", "text/srt", "application/srt"})

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_CUE_TIMING = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->.*$", re.MULTILINE)
_CUE_NUMBER = re.compile(r"^\s*\d+\s*$", re.MULTILINE)
_VTT_HEADER = re.compile(r"\AWEBVTT[^\n]*\n", re.IGNORECASE)
_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Incoming:
    """One turn's input. `files` is decoded file text as (name, text); `notes` names skipped files."""

    text: str
    context: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """Splits a message into the user's text, platform context, files and the JSON-output wish."""
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
        return None, f"I can't read {name} ({kind}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it; paste the part you need instead."
    text = data.decode("utf-8", errors="replace")
    return (strip_cues(text) if kind in CUE_TYPES else text), None


def strip_cues(text: str) -> str:
    """Subtitle files: drop the header, cue numbers and timings, keep the spoken lines."""
    body = _VTT_HEADER.sub("", text.replace("\r\n", "\n"))
    body = _CUE_NUMBER.sub("", _CUE_TIMING.sub("", body))
    return _BLANK_RUNS.sub("\n\n", body).strip()


def word_count(text: str) -> int:
    return len((text or "").split())
