"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts (org policy, agent knowledge) are platform
  instructions for the model, never the request and never config to review.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the earlier review comes from this agent's checkpointer).
- A text file part (inline bytes, at most 5 MB) is read as pasted config under a `# file: <name>` line.
- Another step's envelope (JSON with a `config` or `text` field) is accepted in place of free text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_PASTE_CHARS = 100_000
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_TYPES = ("text/", "application/json", "application/x-yaml", "application/yaml", "application/x-hcl")
TEXT_SUFFIXES = (".yml", ".yaml", ".json", ".tf", ".hcl", ".txt", ".rules", ".md")
ENVELOPE_FIELDS = ("config", "text", "source")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def _is_text(media_type: str, name: str) -> bool:
    media = (media_type or "").casefold()
    return media.startswith(TEXT_TYPES) or (name or "").casefold().endswith(TEXT_SUFFIXES)


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
            name = part.filename or "attached file"
            if kind == "raw" and _is_text(part.media_type, name) and len(part.raw) <= MAX_FILE_BYTES:
                files.append(f"# file: {name}\n{part.raw.decode('utf-8', errors='replace')}")
            else:
                notes.append(f"I can't read {name}; paste its text instead.")
    user = current_message(texts[0]) if texts else ""
    if files:
        user = "\n\n".join([user, *files]) if user.strip() else "\n\n".join(files)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, tuple(notes))


def strip_json_words(text: str) -> str:
    return _JSON_WORDS.sub(" ", text or "").strip()


def from_envelope(text: str) -> str | None:
    """The config inside another agent's envelope, or None when `text` is not one (a Datadog monitor
    JSON is config, not an envelope)."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or "query" in data or "summary" not in data:
        return None
    for name in ENVELOPE_FIELDS:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value
    for item in data.get("items") or []:
        value = item.get("config") if isinstance(item, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    return None


def clip(text: str) -> tuple[str, str | None]:
    """(text within the paste limit, a note when it was cut at a line break)."""
    if len(text) <= MAX_PASTE_CHARS:
        return text, None
    cut = text.rfind("\n", 0, MAX_PASTE_CHARS)
    kept = text[:cut if cut > 0 else MAX_PASTE_CHARS]
    return kept, (f"The paste is {len(text):,} characters; only the first {len(kept):,} (up to a line break) were "
                  "reviewed.")
