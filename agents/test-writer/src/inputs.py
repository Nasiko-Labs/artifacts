"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never source code and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier sources and results come from this agent's own checkpointer).
- An A2A file part (inline text bytes) from an API client is treated as pasted text under a `# file:` label.
- Another agent's envelope (JSON with `items[].source` or `summary`) is accepted as the source text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_MEDIA = ("text/", "application/json", "application/x-diff", "application/x-patch")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words (plus any file parts), policy parts, and whether JSON was asked."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def strip_markers(text: str) -> str:
    """Fence markers removed from untrusted input, so it can't close or open a data block."""
    return _MARKER.sub("", text or "")


def fence_text(name: str, text: str) -> str:
    return f"<<<{name}>>>\n{strip_markers(text)}\n>>>END_{name}<<<"


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; file parts are pasted text."""
    if message is None:
        return Incoming("")
    texts: list[str] = []
    files: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "raw":
            files.append(_file_text(part))
    user = current_message(texts[0]) if texts else ""
    pasted = "\n\n".join([user, *[f for f in files if f]]).strip() if files else user
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(pasted, tuple(t for t in texts[1:] if t.strip()), wants)


def _file_text(part: Any) -> str:
    media = (part.media_type or "text/plain").casefold()
    if len(part.raw) > MAX_FILE_BYTES or not media.startswith(TEXT_MEDIA):
        return ""
    body = part.raw.decode("utf-8", errors="replace")
    name = " ".join((part.filename or "pasted-file").split())
    return f"# file: {name}\n{body}"


def from_envelope(text: str) -> str | None:
    """The source text inside another agent's envelope (`items[].source`, else `summary`), or None."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("items") if isinstance(data.get("items"), list) else []
    sources = [str(i["source"]) for i in items if isinstance(i, dict) and isinstance(i.get("source"), str)]
    if any(s.strip() for s in sources):
        return "\n\n".join(s for s in sources if s.strip())
    summary = data.get("summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else None
