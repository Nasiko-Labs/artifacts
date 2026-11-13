"""Pure: what the user gave this turn, read from the A2A message parts.

- The user's own words are the FIRST text part. Later text parts are org policy or agent knowledge: they
  reach the model as instructions (a glossary, "always formal for customers"), never as text to translate.
- In a session with prior turns the text arrives as "{history}\\n\\nCurrent message: {text}". The text after
  the last marker is this turn's message; the history before it is parsed into turns (`history.py`) and is
  this agent's only memory: it keeps nothing between messages.
- A data part holding a JSON object is an envelope from another agent; message metadata {"output": "json"}
  asks for the envelope as the reply text (a workflow step).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import history
from history import HistoryTurn

CURRENT_MESSAGE_MARKER = history.CURRENT_MESSAGE_MARKER
FILE_NOTE = "I can't read attached files yet; paste the text into the message instead."


@dataclass(frozen=True)
class Incoming:
    text: str
    context: tuple[str, ...] = ()
    data: dict | None = None
    as_json: bool = False
    notes: tuple[str, ...] = ()
    history: tuple[HistoryTurn, ...] = ()


def current_message(raw: str) -> str:
    return history.split(raw)[1]


def read_message(message: Any) -> Incoming:
    """Duck-typed a2a 1.x Message → Incoming."""
    if message is None:
        return Incoming("")
    texts: list[str] = []
    data: dict | None = None
    notes: list[str] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "data" and data is None:
            value = MessageToDict(part.data)
            data = value if isinstance(value, dict) else None
        elif kind in ("raw", "url") and FILE_NOTE not in notes:
            notes.append(FILE_NOTE)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    as_json = str(metadata.get("output") or "").casefold() == "json"
    block, user = history.split(texts[0]) if texts else ("", "")
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), data, as_json, tuple(notes),
                    history.parse(block))
