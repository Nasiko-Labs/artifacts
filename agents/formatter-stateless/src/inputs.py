"""Pure: what the user gave this turn, read from the A2A message parts.

- The user's own words (and pasted JSON) are the FIRST text part. Later text parts are org policy and
  agent knowledge: ignored, since there is no model to instruct.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; the text after the last
  marker is this turn's request, and the history before it is parsed into turns (history.py). This agent
  keeps no state: a follow-up re-renders the input found in those turns (intake.recall).
- A JSON/Markdown/text file part (an API client can send one) is appended to the text as data.
- Message metadata `{"output": "json"}` asks for the envelope as the text part (workflow steps).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import history
from history import Turn

MAX_CHARS = 100_000
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"application/json", "text/plain", "text/markdown", "text/x-markdown"})
TEXT_SUFFIXES = {".json": "application/json", ".md": "text/markdown", ".markdown": "text/markdown",
                 ".txt": "text/plain"}


@dataclass(frozen=True)
class Incoming:
    text: str
    wants_json: bool = False
    notes: tuple[str, ...] = ()
    history: tuple[Turn, ...] = ()     # the platform's history slice: this agent's only memory


def read_parts(parts: Any, metadata: dict | None = None) -> Incoming:
    """The user's text (first text part) plus any readable file parts (duck-typed a2a parts)."""
    texts: list[str] = []
    files: list[str] = []
    notes: list[str] = []
    for part in parts or ():
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind in ("raw", "url"):
            name = part.filename or "the attached file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [decoded] if decoded is not None else []
            notes += [note] if note else []
    block, user = history.split(texts[0]) if texts else ("", "")
    text = "\n\n".join(p for p in (user.strip(), *files) if p.strip())
    wants_json = str((metadata or {}).get("output", "")).casefold() == "json"
    return Incoming(text=text, wants_json=wants_json, notes=tuple(notes), history=history.parse(block))


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    declared = (media_type or "").split(";")[0].strip().casefold()
    kind = declared if declared in TEXT_TYPES else TEXT_SUFFIXES.get(PurePath(name).suffix.casefold(), declared)
    if data is None or kind not in TEXT_TYPES:
        return None, f"I can't read {name} ({kind or 'unknown type'}); paste its JSON instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB; paste a smaller part."
    return data.decode("utf-8", errors="replace"), None
