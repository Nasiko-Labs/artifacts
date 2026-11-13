"""Pure: what the user gave this turn, read from the A2A message, and how it splits into brief and instruction.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  instructions for the model, never brief material and never grounding.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; the text after the last
  marker is this turn's message and the history before it is parsed into turns (`history.py`): the only
  memory of earlier turns (nothing is checkpointed).
- Another agent's envelope (JSON with `summary` and/or `items[]`) becomes labeled brief material.
- A short first paragraph (or first line) that asks for the PRD is the instruction, not brief text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from history import Turn, parse, split

MIN_BRIEF_WORDS = 40
COMPLETE_BRIEF_MIN_WORDS = 20      # a shorter brief is enough when it names most of what a PRD needs
COMPLETE_BRIEF_MIN_SIGNALS = 4     # of: who, need, constraint, deadline, success metric
MAX_BRIEF_CHARS = 30_000
INSTRUCTION_MAX_CHARS = 300
SPLIT_MIN_REST_CHARS = 500
DEFAULT_CAP, ONE_PAGE_CAP, DETAILED_CAP = 900, 400, 1_500

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_REQUEST_CUE = re.compile(r"\b(?:prd|product requirements?|spec|write|draft|one page|detailed|keep it)\b",
                          re.IGNORECASE)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_BRIEF_SIGNALS = (
    re.compile(r"\b(?:admins?|users?|customers?|teams?|teammates?|managers?|developers?|engineers?|compan(?:y|ies)|"
               r"buyers?|merchants?|sellers?|operators?|staff|members?|people)\b", re.IGNORECASE),     # who
    re.compile(r"\b(?:wants?|needs?|asks?|asking|struggl\w*|slow|pain\w*|problems?|cannot|can't|so that|"
               r"by hand|manually)\b", re.IGNORECASE),                                                  # need
    re.compile(r"\b(?:up to|at most|no more than|at least|must|should|only|expires?|limit\w*|within|"
               r"max(?:imum)?)\b", re.IGNORECASE),                                                      # constraint
    re.compile(rf"\b(?:launch\w*|deadline|due|release|target date|by {_MONTH}|before {_MONTH}|q[1-4])\b|"
               rf"\b{_MONTH}\b", re.IGNORECASE),                                                        # deadline
    re.compile(r"\d+(?:\.\d+)?\s?%|\b(?:success|metrics?|kpis?|conversion|adoption|retention|churn|nps)\b",
               re.IGNORECASE),                                                                          # metric
)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_ONE_PAGE = re.compile(r"\bone[- ]pager?\b|\bone page\b|\bshort\b|\bbrief one\b", re.IGNORECASE)
_DETAILED = re.compile(r"\bdetailed\b|\bin detail\b|\bthorough\b", re.IGNORECASE)
_AUDIENCE = re.compile(
    r"\bfor (?:the |our |an? )?([\w&/ -]{1,40}?(?:teams?|customers?|users?|admins?|developers?|engineers?|"
    r"partners?|org|organi[sz]ation|managers?|buyers?|merchants?|sellers?|operators?|staff))\b"
    r"|\b(b2b|b2c|internal|enterprise|smb)\b", re.IGNORECASE)
_TITLE = re.compile(r"\b(?:prd|spec|requirements)\s+(?:for|on|about)\s+(?:the |a |an |our )?([^,.;:\n]{2,60})",
                    re.IGNORECASE)


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
    brief: str
    instruction: str = ""
    enveloped: bool = False


@dataclass(frozen=True)
class Instruction:
    cap: int = DEFAULT_CAP
    audience: str = ""
    title: str = ""


def read_message(message: Any) -> Incoming:
    """The first text part is the user's; later text parts are policy; metadata may ask for JSON."""
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    block, user = split(texts[0]) if texts else ("", "")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, parse(block), bool(block.strip()))


def enough_brief(brief: str) -> bool:
    """Enough to write a PRD without inventing most of it: 40+ words, or 20+ words that name at least four of
    who it is for, the need, a constraint, a deadline and a success metric."""
    words = word_count(brief)
    if words >= MIN_BRIEF_WORDS:
        return True
    signals = sum(1 for rx in _BRIEF_SIGNALS if rx.search(brief or ""))
    return words >= COMPLETE_BRIEF_MIN_WORDS and signals >= COMPLETE_BRIEF_MIN_SIGNALS


def word_count(text: str) -> int:
    return len((text or "").split())


def from_envelope(text: str) -> str | None:
    """Labeled brief material from another agent's envelope, or None when `text` is not one."""
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not ({"summary", "items"} & data.keys()):
        return None
    lines = [f"summary: {data['summary'].strip()}"] if isinstance(data.get("summary"), str) else []
    for n, item in enumerate(data.get("items") or [], start=1):
        if isinstance(item, str) and item.strip():
            lines.append(f"items[{n}]: {item.strip()}")
        elif isinstance(item, dict):
            lines += [f"items[{n}].{k}: {v.strip()}" for k, v in item.items() if isinstance(v, str) and v.strip()]
    return "\n".join(lines) or None


def split_request(text: str) -> Request:
    """(brief, instruction) from one pasted message."""
    stripped = (text or "").strip()
    head, rest, by_paragraph = _split_head(stripped)
    if head and len(head) <= INSTRUCTION_MAX_CHARS:
        enveloped = from_envelope(rest)
        if _is_instruction(head, rest, enveloped, by_paragraph):
            return Request(enveloped or rest, head.rstrip(":").strip(), enveloped is not None)
    whole = from_envelope(stripped)
    return Request(whole or stripped, "", whole is not None)


def _split_head(text: str) -> tuple[str, str, bool]:
    """(first paragraph or line, the rest, split at a paragraph break), or ('', '', False)."""
    match = _PARAGRAPH_BREAK.search(text)
    if match:
        return text[:match.start()].strip(), text[match.end():].strip(), True
    first, _, after = text.partition("\n")
    if after.strip():
        return first.strip(), after.strip(), False
    head, colon, tail = text.partition(":")  # 'Write a PRD from this brief: <brief>' on one line
    if colon and tail.strip():
        return head.strip(), tail.strip(), False
    return "", "", False


def _is_instruction(head: str, rest: str, enveloped: str | None, by_paragraph: bool) -> bool:
    if enveloped is not None:
        return True
    if by_paragraph and len(rest) >= SPLIT_MIN_REST_CHARS:
        return True
    return bool(_REQUEST_CUE.search(head)) and word_count(rest) >= MIN_BRIEF_WORDS // 2


def parse_instruction(instruction: str) -> Instruction:
    """Word cap, audience and title recognised in code."""
    text = instruction or ""
    cap = DETAILED_CAP if _DETAILED.search(text) else ONE_PAGE_CAP if _ONE_PAGE.search(text) else DEFAULT_CAP
    found = _AUDIENCE.search(text)
    audience = (found.group(1) or found.group(2)).strip() if found else ""
    titled = _TITLE.search(text)
    title = titled.group(1).strip() if titled else ""
    if title and audience and audience.casefold() in title.casefold():
        title = ""  # "PRD for the platform team" names the audience, not the product
    return Instruction(cap=cap, audience=audience, title=title)
