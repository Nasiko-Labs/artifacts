"""Pure: what the user gave this turn, read from the A2A message, and the data fence.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never code and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier code and results come from this agent's own checkpointer).
- A2A file parts (inline bytes, a text type, at most 5 MB) are read as pasted text; the file name becomes the
  `# file:` label. Never required: the chat UI cannot attach files.
Copied from the security-reviewer agent's inputs.py and trimmed; owned by the security-gate agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "application/json", "text/csv",
                        "text/x-diff", "text/x-patch", "application/x-diff", "application/x-patch"})
TEXT_SUFFIXES = frozenset({".txt", ".md", ".json", ".csv", ".diff", ".patch"})

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, decoded files, platform policy parts, JSON output asked."""

    text: str
    policy: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; file parts are pasted text."""
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
            name = part.filename or "attached-file"
            decoded, note = decode_file(name, part.media_type, part.raw if kind == "raw" else None)
            files += [(name, decoded)] if decoded is not None else []
            notes += [note] if note else []
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), tuple(files), tuple(notes), wants)


def decode_file(name: str, media_type: str | None, data: bytes | None) -> tuple[str | None, str | None]:
    """(text, None) for a readable text file; (None, note) for anything else."""
    declared = (media_type or "").split(";")[0].strip().casefold()
    readable = declared in TEXT_TYPES or PurePath(name).suffix.casefold() in TEXT_SUFFIXES or declared.startswith("text/")
    if data is None or not readable:
        return None, f"I can't read {name} ({declared or 'unknown type'}); paste its text instead."
    if len(data) > MAX_FILE_BYTES:
        return None, f"{name} is over 5 MB, so I skipped it; paste the part you want reviewed."
    return data.decode("utf-8", errors="replace"), None


def assemble(text: str, files: tuple[tuple[str, str], ...]) -> str:
    """The message text plus each file as a `# file:` section."""
    pieces = [text.rstrip()] if text.strip() else []
    pieces += [f"# file: {name}\n{body.rstrip()}" for name, body in files if body.strip()]
    return "\n".join(pieces)
