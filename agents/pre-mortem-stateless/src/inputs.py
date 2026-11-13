"""Pure: what the user gave this turn, read from the A2A message, and how it splits into plan and context.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never plan text.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}". The text after the last marker
  is this turn; the history before it is kept as turns (`history.py`) and is the agent's only memory: a
  follow-up re-reads the earlier plan and pre-mortem from it (`recall.py`).
- Another agent's envelope (JSON with `memo_markdown`, `text` or `summary`) is accepted as the plan.
- Context lines (`Context:`, `Team:`, `Deadline:`, `Dependencies:`) and a leading request line ("Pre-mortem
  this PRD, team of 4, launch Dec 15") become CONTEXT: grounding facts, never numbered plan lines.
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

ENVELOPE_FIELDS = ("memo_markdown", "text", "summary")
REQUEST_LINE_MAX_CHARS = 300
COLON_PREFIX_MAX_CHARS = 120

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(context|team|deadline|dependencies|depends on)\s*:\s*(.*)$", re.IGNORECASE)
_REQUEST = re.compile(r"pre-?mortem|what could go wrong|failure modes?|\brisks? (?:in|of) this\b|focus on|as json|"
                      r"team of \d|\btop \d\b|\b\d (?:stories|failures?)\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, whether JSON output was asked, and the
    earlier turns the platform sent with it (oldest first)."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class Request:
    plan: str
    context: tuple[str, ...] = ()
    instruction: str = ""


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


def word_count(text: str) -> int:
    return len((text or "").split())


def from_envelope(text: str) -> str | None:
    """The plan text inside another agent's envelope, or None when `text` is not one."""
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
    """Plan, context and the user's request line, from one pasted message. Label lines (`Team:`, `Deadline:`,
    a `Context:` block) are context only before or after the plan; inside the plan they stay plan lines."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return Request(plan=enveloped)
    instruction, lines = _request_line((text or "").strip().splitlines())
    lead, lines = _leading_context(lines)
    trail, lines = _trailing_context(lines)
    context = ([instruction] if instruction else []) + lead + trail
    return Request(plan="\n".join(lines).strip(), context=tuple(context), instruction=instruction)


def _request_line(lines: list[str]) -> tuple[str, list[str]]:
    """(request, the rest): a first line that asks for the pre-mortem is the request, not plan text."""
    if not lines:
        return "", []
    first, rest = lines[0], lines[1:]
    head, colon, tail = first.partition(":")
    if colon and len(head) <= COLON_PREFIX_MAX_CHARS and _REQUEST.search(head) and not _LABEL.match(first):
        return head.strip(), ([tail.strip()] if tail.strip() else []) + rest
    if len(first) <= REQUEST_LINE_MAX_CHARS and _REQUEST.search(first) and rest:
        return first.strip().rstrip(":"), rest
    return "", lines


def _is_context_label(line: str) -> bool:
    label = _LABEL.match(line)
    return bool(label) and label.group(1).casefold() == "context"


def _leading_context(lines: list[str]) -> tuple[list[str], list[str]]:
    taken: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not _LABEL.match(line):
            break
        taken.append(line.strip())
        i += 1
        if _is_context_label(line):
            while i < len(lines) and lines[i].strip():
                taken.append(lines[i].strip())
                i += 1
    return [t for t in taken if not _empty_label(t)], lines[i:]


def _trailing_context(lines: list[str]) -> tuple[list[str], list[str]]:
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    j = end - 1
    while j >= 0 and lines[j].strip() and not _LABEL.match(lines[j]):
        j -= 1
    taken: list[str] = []
    if j >= 0 and _is_context_label(lines[j]):
        taken, end = [line.strip() for line in lines[j:end]], j
    while end and _LABEL.match(lines[end - 1]):
        taken.insert(0, lines[end - 1].strip())
        end -= 1
    return [t for t in taken if not _empty_label(t)], lines[:end]


def _empty_label(line: str) -> bool:
    label = _LABEL.match(line)
    return bool(label) and not label.group(2).strip()
