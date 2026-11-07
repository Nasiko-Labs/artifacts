"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts (org policy, agent knowledge) are
  organization instructions for the model, never the request and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the earlier config comes from this agent's checkpointer).
- A text file part (inline bytes, ≤ 5 MB) is read as pasted text labelled `# file: <name>`.
- Another step's envelope (JSON with `items[].config` or `summary`) is accepted as the config.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_CONFIG_CHARS = 30_000
MAX_FILE_BYTES = 5 * 1024 * 1024
FENCE_OPEN, FENCE_CLOSE = "<<<INPUT>>>", "<<<END>>>"
TEXT_TYPES = ("text/", "application/json", "application/x-yaml", "application/yaml")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    text: str
    org: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "raw" and _is_text(part.media_type, part.filename) and len(part.raw) <= MAX_FILE_BYTES:
            files.append(f"# file: {part.filename or 'attached file'}\n{part.raw.decode('utf-8', errors='replace')}")
    user = current_message(texts[0]) if texts else ""
    if files:
        user = "\n\n".join([user, *files]) if user.strip() else "\n\n".join(files)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants)


def _is_text(media_type: str, name: str) -> bool:
    media = (media_type or "").casefold()
    return media.startswith(TEXT_TYPES) or (name or "").casefold().endswith((".conf", ".json", ".txt", ".md"))


def strip_json_words(text: str) -> str:
    return _JSON_WORDS.sub(" ", text or "").strip()


def from_envelope(text: str) -> str | None:
    """The config inside another agent's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or "Statement" in data or "Effect" in data:
        return None  # an IAM policy is a config, not an envelope
    for item in data.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("config"), str) and item["config"].strip():
            return item["config"]
    summary = data.get("summary")
    return summary if isinstance(summary, str) and summary.strip() else None


def fence_text(text: str) -> str:
    """Untrusted text between markers, with any marker inside it removed first."""
    clean = (text or "").replace(FENCE_OPEN, "").replace(FENCE_CLOSE, "")
    return f"{FENCE_OPEN}\n{clean}\n{FENCE_CLOSE}"
