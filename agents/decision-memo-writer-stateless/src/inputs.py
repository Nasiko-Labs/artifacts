"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never brief text.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}". The text after the last
  marker is this turn; the history before it is kept as turns (`history.py`) and is the agent's only memory:
  a follow-up re-reads the earlier brief, its changes and the last memo from it (`recall.py`).
- Another agent's envelope (JSON with `summary`, `items` or `text`) is read as data, labelled as coming
  from a previous step.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

import history
from history import Turn

ENVELOPE_TEXT_FIELDS = ("summary", "text", "memo_markdown")
ENVELOPE_MAX_CHARS = 3_000
FROM_STEP = "From a previous step: "

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


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
    user = user.strip()
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


def without_json_words(text: str) -> str:
    return _JSON_WORDS.sub("", text or "").strip()


def _envelope_lines(data: dict) -> list[str]:
    lines = [FROM_STEP + " ".join(str(data[k]).split()) for k in ENVELOPE_TEXT_FIELDS
             if isinstance(data.get(k), str) and data[k].strip()]
    for item in data.get("items") or () if isinstance(data.get("items"), list) else ():
        lines.append(FROM_STEP + (json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item))
    out, size = [], 0
    for line in lines:
        size += len(line)
        if size > ENVELOPE_MAX_CHARS:
            break
        out.append(line)
    return out


def _as_envelope(body: str) -> dict | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and any(k in data for k in ("summary", "items", "text", "memo_markdown")):
        return data
    return None


def with_envelope_as_data(text: str) -> str:
    """A pasted envelope (fenced or the whole message) becomes `Data:` lines; anything else is unchanged."""
    stripped = (text or "").strip()
    fenced = _FENCED.search(stripped)
    body, rest = (fenced.group(1), stripped[:fenced.start()] + stripped[fenced.end():]) if fenced else (stripped, "")
    data = _as_envelope(body) if body.startswith("{") else None
    if data is None:
        return text
    return (rest.strip() + "\n\nData:\n" + "\n".join(_envelope_lines(data))).strip()
