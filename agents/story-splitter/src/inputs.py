"""Pure: what the user gave this turn, read from the A2A message, and how it splits into epic, constraints and
options.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never epic text.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; only the text after the last
  marker is used (earlier epics and stories come from this agent's own checkpointer).
- A previous step's envelope (`prd_markdown`, `requirements[]`, `text`, `summary`) is read as epic text.
- Options parsed in code: `max N stories` (3-12, default 8), `personas: a, b`, `sprint-sized`.
- `Constraints:` / `Out of scope:` / `Not in scope:` text (to the end of its paragraph) becomes CONSTRAINTS.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
DEFAULT_MAX_STORIES = 8
MIN_MAX_STORIES, HARD_MAX_STORIES = 3, 12

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_MAX = re.compile(r"\b(?:max(?:imum)?|at most|up to)\s+(\d{1,2})\s+stor(?:y|ies)\b", re.IGNORECASE)
_PERSONAS = re.compile(r"^\s*personas?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_SPRINT = re.compile(r"\bsprint[- ]sized\b", re.IGNORECASE)
_CONSTRAINT = re.compile(r"\b(constraints?|out of scope|not in scope)\s*:\s*", re.IGNORECASE)
_REQUEST_LEAD = re.compile(r"^\s*(?:please\s+)?(?:split|break|turn|slice)\b[^:\n]{0,120}:\s*", re.IGNORECASE)
_SPLIT_LIST = re.compile(r"\s*(?:,|;|\band\b)\s*")


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, and whether JSON output was asked."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    epic: str
    constraints: tuple[str, ...] = ()       # the out-of-scope / constraint statements, verbatim
    max_stories: int = DEFAULT_MAX_STORIES
    personas: tuple[str, ...] = ()
    sprint_sized: bool = False


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


def clamp_max(value: int | None) -> int:
    if value is None:
        return DEFAULT_MAX_STORIES
    return max(MIN_MAX_STORIES, min(HARD_MAX_STORIES, value))


def from_envelope(text: str) -> str | None:
    """The epic text inside a previous step's envelope, or None when `text` is not one."""
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
    pieces = [str(data[k]).strip() for k in ("prd_markdown", "text", "summary")
              if isinstance(data.get(k), str) and data[k].strip()][:1]
    for item in data.get("requirements") or ():
        line = item.get("text") if isinstance(item, dict) else item
        if isinstance(line, str) and line.strip():
            pieces.append(f"- {line.strip()}")
    return "\n".join(pieces) if pieces else None


def parse_request(text: str) -> Request:
    """Epic, constraints and options from one message (options are removed from the epic text)."""
    enveloped = from_envelope(text)
    body = enveloped if enveloped is not None else (text or "")
    found_max = _MAX.search(body)
    personas = _PERSONAS.search(body)
    names = tuple(p.strip() for p in _SPLIT_LIST.split(personas.group(1)) if p.strip()) if personas else ()
    body = _PERSONAS.sub("", _MAX.sub("", body))
    sprint = bool(_SPRINT.search(body))
    body = _SPRINT.sub("", body)
    body, constraints = _take_constraints(body)
    if enveloped is None:
        body = _REQUEST_LEAD.sub("", body, count=1)
    return Request(epic=body.strip(), constraints=constraints,
                   max_stories=clamp_max(int(found_max.group(1)) if found_max else None),
                   personas=names, sprint_sized=sprint)


def _take_constraints(body: str) -> tuple[str, tuple[str, ...]]:
    """Each `Out of scope:` (etc.) runs to the end of its paragraph; the epic keeps everything else."""
    constraints: list[str] = []
    while (match := _CONSTRAINT.search(body)) is not None:
        end = body.find("\n\n", match.end())
        end = len(body) if end == -1 else end
        statement = body[match.end():end].strip()
        if statement:
            label = match.group(1).casefold()
            prefix = "Constraint" if label.startswith("constraint") else "Out of scope"
            constraints.append(f"{prefix}: {statement}")
        body = (body[:match.start()].rstrip() + "\n\n" + body[end:].lstrip()).strip()
    return body, tuple(constraints)


def out_of_scope_items(constraints: tuple[str, ...]) -> list[str]:
    """The individual out-of-scope items ("syncing from other CRMs"), for the drop check."""
    items: list[str] = []
    for line in constraints:
        if line.startswith("Out of scope: "):
            items += [p.strip(" .") for p in re.split(r"[;,\n]|\.\s", line[len("Out of scope: "):]) if p.strip(" .")]
    return items
