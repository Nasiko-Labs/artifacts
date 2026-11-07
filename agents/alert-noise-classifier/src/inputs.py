"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A file part (proto `raw` bytes; text/csv, text/plain, application/json, ≤ 5 MB) is decoded as the alert
  data; it is accepted when present, never required.
- JSON mode: "as json" / "json only" in the instruction, or message metadata {"output": "json"}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from detect import split

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "text/tab-separated-values",
                        "application/vnd.ms-excel", "text/markdown"})
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    file_text: str = ""
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def wants_json(text: str) -> bool:
    """Only the instruction switches modes, never words inside the pasted data."""
    return bool(_JSON_WORDS.search(split(text).instruction)) if text else False


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts, files, notes = [], [], []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            decoded, note = decode_file(part.filename or "the attached file", part.media_type,
                                        part.raw if kind == "raw" else None)
            files += [decoded] if decoded else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    as_json = str(metadata.get("output") or "").casefold() == "json" or wants_json(user)
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), files[0] if files else "", tuple(notes), as_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or name.casefold().endswith((".csv", ".tsv", ".txt", ".json", ".log"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); send the alerts as CSV, JSON or text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; send a shorter period."
    return data.decode("utf-8", errors="replace"), None
