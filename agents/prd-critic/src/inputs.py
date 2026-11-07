"""Pure: what the user gave this turn, read from the A2A message, and how it splits into request and PRD.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never PRD text.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (the stored PRD and review come from this agent's own checkpointer).
- Another agent's envelope (JSON with `prd_markdown` or `text`) is accepted as the PRD.
- A leading request line ("Review this PRD (lenient, focus: metrics):") is the instruction, never PRD text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
ENVELOPE_FIELDS = ("prd_markdown", "text")
REQUEST_LINE_MAX_CHARS = 300
COLON_PREFIX_MAX_CHARS = 120
FOCUS_KINDS = ("blockers", "metrics", "requirements", "sections", "questions")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_REQUEST = re.compile(r"\b(?:review|critique|critic|score|grade|assess|evaluate|check)\b|\bprd\b|\blenient\b|"
                      r"\bstrict\b|\bfocus\s*:|\bas json\b", re.IGNORECASE)
_HEADING_LIKE = re.compile(r"^\s*(?:#|\*\*|__)")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_FOCUS = re.compile(r"\bfocus\s*(?::|on)\s*(blockers?|metrics?|requirements?|sections?|questions?)", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, and whether JSON output was asked."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    prd: str
    instruction: str = ""


@dataclass(frozen=True)
class Options:
    lenient: bool = False
    focus: str = ""   # "" or one of FOCUS_KINDS


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


def from_envelope(text: str) -> str | None:
    """The PRD inside another agent's envelope, or None when `text` is not one."""
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
    for name in ENVELOPE_FIELDS:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def split_request(text: str) -> Request:
    """(PRD, request line) from one message. A first line that asks for the review is the request."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return Request(prd=enveloped)
    lines = (text or "").strip().splitlines()
    if not lines:
        return Request(prd="")
    first, rest = lines[0], lines[1:]
    if not _HEADING_LIKE.match(first):
        head, colon, tail = first.partition(":")
        if colon and len(head) <= COLON_PREFIX_MAX_CHARS and _REQUEST.search(head) and not tail.strip():
            return Request(prd="\n".join(rest).strip(), instruction=head.strip())
        if len(first) <= REQUEST_LINE_MAX_CHARS and _REQUEST.search(first) and rest and _is_request(first):
            return Request(prd="\n".join(rest).strip(), instruction=first.strip().rstrip(":"))
    return Request(prd="\n".join(lines).strip())


def _is_request(line: str) -> bool:
    """A request line reads as an instruction: short, and not itself a sentence of the PRD."""
    return len(line.split()) <= 20 and not line.rstrip().endswith(".")


def options(instruction: str) -> Options:
    text = instruction or ""
    focus = _FOCUS.search(text)
    kind = focus.group(1).casefold() if focus else ""
    kind = next((k for k in FOCUS_KINDS if k.startswith(kind.rstrip("s"))), "") if kind else ""
    return Options(lenient=bool(re.search(r"\blenient\b", text, re.IGNORECASE)), focus=kind)
