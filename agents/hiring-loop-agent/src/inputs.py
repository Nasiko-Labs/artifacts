"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  system instructions for the model, never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier evidence and drafts come from this agent's own checkpointer).
- "as json" / "json only" in the message, or metadata {"output": "json"}, asks for the fenced envelope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


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
