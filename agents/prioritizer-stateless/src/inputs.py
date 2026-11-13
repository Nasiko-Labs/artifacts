"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge: context
  for judging impact only, never tasks and never a re-weighting.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}". The text after the last marker
  is this turn; the history before it is kept as turns (`history.py`) and is the agent's only memory: a
  follow-up re-reads the earlier ranking or list from it.
- "as json" / "json only" in the text, or message metadata {"output": "json"}, asks for the envelope as text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import history
from history import Turn

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, whether JSON output was asked, and the
    earlier turns the platform sent with it (oldest first)."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[Turn, ...] = ()


def from_text(raw: str, policy: Sequence[str] = (), json_metadata: bool = False) -> Incoming:
    turns, user = history.split(raw)
    wants = json_metadata or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in policy if t.strip()), wants, turns)


def read_message(message: Any) -> Incoming:
    """The first text part is the user's (with history); later text parts are policy; metadata may ask for JSON."""
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    as_json = str(metadata.get("output") or "").casefold() == "json"
    return from_text(texts[0] if texts else "", texts[1:], as_json)
