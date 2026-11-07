"""Pure: what the user gave this turn, read from the A2A message.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never brief text.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the
  last marker is used (earlier memos come from this agent's own checkpointer).
- Another agent's envelope (JSON with `summary`, `items` or `text`) is read as data, labelled as coming
  from a previous step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
ENVELOPE_TEXT_FIELDS = ("summary", "text", "memo_markdown")
ENVELOPE_MAX_CHARS = 3_000
FROM_STEP = "From a previous step: "

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


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
    user = current_message(texts[0]).strip() if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants)


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
