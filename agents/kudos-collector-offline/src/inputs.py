"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never records. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the
  text after the last marker is used.
- A file part (inline bytes; text/csv, text/plain, application/json, text/markdown, <= 5 MB) is read as more paste.
- A previous step's envelope (JSON) is accepted: `text`, `records` (strings), `prs`, `praise`, `shipped`
  (strings or lists of strings) and `aliases` are read; anything else is ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "text/markdown"})
SECTION_FIELDS = (("prs", "PRs:"), ("praise", "Praise:"), ("shipped", "Shipped:"), ("aliases", "Aliases:"))
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


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
    user = from_envelope(current_message(texts[0]) if texts else "")
    joined = "\n".join(p for p in (user, *(from_envelope(f) for f in files)) if p.strip())
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants_json = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user[:400]))
    return Incoming(joined, tuple(t for t in texts[1:] if t.strip()), tuple(notes), wants_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind in TEXT_TYPES or name.casefold().endswith((".csv", ".txt", ".json", ".md"))
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste the records as text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; split it and send the parts with \"add these\"."
    return data.decode("utf-8", errors="replace"), None


def _lines(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value)
    return ""


def from_envelope(text: str) -> str:
    """The paste inside a JSON envelope, as sectioned text; any other text unchanged."""
    body = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not body.startswith("{"):
        return text or ""
    try:
        data = json.loads(body)
    except ValueError:
        return text or ""
    if not isinstance(data, dict):
        return text or ""
    parts = [_lines(data.get("text")), _lines(data.get("records"))]
    parts += [f"{label}\n{_lines(data[key])}" for key, label in SECTION_FIELDS if _lines(data.get(key)).strip()]
    found = "\n".join(p for p in parts if p.strip())
    return found or (text or "")
