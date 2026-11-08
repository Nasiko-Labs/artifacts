"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part; later text parts (org policy, agent knowledge) are platform
  context, never data. With history the first part is "{history}\\n\\nCurrent message: {text}"; only the text
  after the last marker is used.
- A text file part (inline bytes, at most 5 MB) is read as more pasted output.
- A previous step's envelope (JSON with `blocks: [{type, text}]`) is accepted in place of free text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    file_text: str = ""
    notes: tuple[str, ...] = ()
    wants_json: bool = False

    @property
    def paste(self) -> str:
        return "\n\n".join(p for p in (from_envelope(self.text) or self.text, self.file_text) if p)


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
            name = part.filename or "the attached file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [decoded] if decoded else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants_json = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user[:500]))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), "\n\n".join(files), tuple(notes), wants_json)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    kind = (media_type or "").split(";")[0].strip().casefold()
    readable = kind.startswith("text/") or kind in ("application/json", "") or name.casefold().endswith(".txt")
    if data is None or not readable:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste the kubectl output as text."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; paste the relevant output only."
    return data.decode("utf-8", errors="replace"), None


def from_envelope(text: str) -> str | None:
    """The joined `blocks[].text` of an earlier step's envelope, else None."""
    stripped = (text or "").strip()
    fenced = _FENCED.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return None
    texts = [b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"].strip()]
    return "\n\n".join(texts) if texts else None
