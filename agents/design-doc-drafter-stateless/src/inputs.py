"""Pure: what the user gave this turn, read from the A2A message, and how it splits into requirements,
context (given, never proposed) and pasted guidance.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never requirements and never evidence.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; the text after the last
  marker is this turn's message and the history before it is parsed into turns (`history.py`): the only
  memory of earlier turns (nothing is checkpointed).
- An inline text FilePart (API clients only) is read as pasted text under a `# file: <name>` label.
- Another step's envelope (JSON with `items[].requirements` or `summary`) is accepted as the requirements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from history import Turn, split
from history import parse as parse_history

MIN_WORDS = 25                      # fewer words of requirements than this is too little to draft from...
DENSE_MIN_WORDS = 12                # ...unless they are this long and list at least DENSE_MIN_CLAUSES separate
DENSE_MIN_CLAUSES = 3               # requirements ("200 writes/s; links never expire; custom aliases")
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_MEDIA = ("text/", "application/json", "application/x-diff", "application/x-patch", "text/csv")
REQUEST_LINE_MAX_CHARS = 300

_CLAUSE_BREAK = re.compile(r"[;\n]|(?<=[.!?])\s+|^\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)
_MARKER = re.compile(r"<<<\s*/?\s*(?:END_)?[A-Z_]{2,40}\s*>>>", re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_LABEL = re.compile(r"^\s*(context|stack|existing stack|constraints given)\s*:\s*(.*)$", re.IGNORECASE)
_GUIDANCE = re.compile(r"^\s*follow this\s*:\s*(.*)$", re.IGNORECASE)
_REQUEST = re.compile(r"design doc|design document|architecture|\badr\b|decision record|requirements|"
                      r"\brfc\b|draft a design", re.IGNORECASE)
_ADR = re.compile(r"\badr\b|decision record", re.IGNORECASE)
_SHORT = re.compile(r"\bshort\b|\bbrief\b|\bconcise\b", re.IGNORECASE)
# "support agents" or a "data model" is not an AI system; a bare "workflow" is too common to count.
_AGENT = re.compile(r"(?<!support )(?<!sales )(?<!service )(?<!human )(?<!user )(?<!travel )\bagents?\b|"
                    r"\bagentic\b|\bllms?\b|\bprompts?\b|\btool calls?\b|\brag\b|\bai assistants?\b|"
                    r"\bchatbots?\b|\ba2a\b|\bmcp\b|\b(?:language|ai|ml|llm) models?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, and whether JSON output was asked."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[Turn, ...] = ()
    had_history: bool = False       # a history block was sent, even one too garbled to parse


@dataclass(frozen=True)
class Request:
    requirements: str
    context: tuple[str, ...] = ()
    guidance: str = ""
    instruction: str = ""
    adr: bool = False
    short: bool = False


def strip_markers(text: str) -> str:
    """Removes anything shaped like one of our fence markers, so the input cannot close a fence."""
    return _MARKER.sub("", text or "")


def fence_text(name: str, body: str) -> str:
    return f"<<<{name}>>>\n{strip_markers(body)}\n<<<END_{name}>>>"


def current_message(raw: str) -> str:
    return split(raw)[1]


def read_message(message: Any) -> Incoming:
    """The first text part is the user's (plus any text file parts); later text parts are policy."""
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    block, user = split(texts[0]) if texts else ("", "")
    files = [_file_text(p) for p in message.parts if p.WhichOneof("content") == "raw"]
    user = "\n\n".join(t for t in [user, *files] if t)
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, parse_history(block), bool(block.strip()))


def _file_text(part: Any) -> str:
    media = (part.media_type or "text/plain").casefold()
    if len(part.raw) > MAX_FILE_BYTES or not media.startswith(TEXT_MEDIA):
        return ""
    body = part.raw.decode("utf-8", errors="replace")
    return f"# file: {part.filename or 'attachment'}\n{body}"


def word_count(text: str) -> int:
    return len((text or "").split())


def enough_to_draft(request: Request) -> bool:
    """25+ words of requirements, or, after an explicit request line ("Draft a design doc for these
    requirements: ..."), a short but concrete list: 12+ words in 3+ separate requirements of 2+ words each."""
    words = word_count(request.requirements)
    if words >= MIN_WORDS:
        return True
    clauses = [c for c in _CLAUSE_BREAK.split(request.requirements) if c and word_count(c) >= 2]
    return bool(request.instruction) and words >= DENSE_MIN_WORDS and len(clauses) >= DENSE_MIN_CLAUSES


def from_envelope(text: str) -> str | None:
    """The requirements inside another step's envelope, or None when `text` is not one."""
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
    items = [i.get("requirements") for i in data.get("items") or () if isinstance(i, dict)]
    found = [str(r).strip() for r in items if isinstance(r, str) and r.strip()]
    if found:
        return "\n".join(found)
    summary = data.get("summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


def split_request(text: str) -> Request:
    """Requirements, given context, pasted guidance and the request line, from one pasted message."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return Request(requirements=strip_markers(enveloped))
    lines = strip_markers(text).strip().splitlines()
    instruction, lines = _request_line(lines)
    guidance, lines = _guidance(lines)
    context, rest = _context(lines)
    return Request(requirements="\n".join(rest).strip(), context=tuple(context), guidance=guidance,
                   instruction=instruction, adr=bool(_ADR.search(instruction)),
                   short=bool(_SHORT.search(instruction)))


def is_agent_system(text: str) -> bool:
    return bool(_AGENT.search(text or ""))


def _request_line(lines: list[str]) -> tuple[str, list[str]]:
    if not lines:
        return "", []
    first, rest = lines[0], lines[1:]
    head, colon, tail = first.partition(":")
    if colon and len(head) <= REQUEST_LINE_MAX_CHARS and _REQUEST.search(head) and not _LABEL.match(first):
        return head.strip(), ([tail.strip()] if tail.strip() else []) + rest
    return "", lines


def _guidance(lines: list[str]) -> tuple[str, list[str]]:
    for i, line in enumerate(lines):
        match = _GUIDANCE.match(line)
        if match:
            return "\n".join([match.group(1), *lines[i + 1:]]).strip(), lines[:i]
    return "", lines


def _context(lines: list[str]) -> tuple[list[str], list[str]]:
    """`Context:` / `Stack:` lines, and a `Context:` block up to the next blank line."""
    context: list[str] = []
    rest: list[str] = []
    in_block = False
    for line in lines:
        label = _LABEL.match(line)
        if label:
            context += [label.group(2).strip()] if label.group(2).strip() else []
            in_block = not label.group(2).strip()
        elif in_block and line.strip():
            context.append(line.strip())
        else:
            in_block = False
            rest.append(line)
    return context, rest
