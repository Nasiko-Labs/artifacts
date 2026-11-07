"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never notes or updates.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier pastes and the prep come from this agent's own checkpointer).
- Another agent's envelope (JSON, Team Pulse or Summarizer style) is read as "recent updates": its text fields
  (`summary`, `text`, `memo_markdown`, and each item's `text`/`what`/`summary`/`quote`) are joined, labeled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
ENVELOPE_TEXT_FIELDS = ("summary", "text", "memo_markdown")
ITEM_TEXT_FIELDS = ("text", "what", "summary", "title", "quote")
MAX_ENVELOPE_ITEMS = 200

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, and whether JSON output was asked."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; metadata may ask for JSON."""
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants)


def word_count(text: str) -> int:
    return len((text or "").split())


def envelope_updates(text: str) -> str | None:
    """The text inside another agent's JSON envelope, one line per field, or None when `text` is not one."""
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
    lines = [str(data[k]).strip() for k in ENVELOPE_TEXT_FIELDS if isinstance(data.get(k), str) and data[k].strip()]
    items = data.get("items") if isinstance(data.get("items"), list) else []
    for item in items[:MAX_ENVELOPE_ITEMS]:
        if isinstance(item, str) and item.strip():
            lines.append(item.strip())
        elif isinstance(item, dict):
            lines += [str(item[k]).strip() for k in ITEM_TEXT_FIELDS
                      if isinstance(item.get(k), str) and item[k].strip()]
    return "\n".join(lines) if lines else None
