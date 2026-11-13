"""Pure: what the user gave this turn, and the optional header lines (version, date, product, audience,
tone, Slack channel, notes link) at the top of a pasted change list.

- The user's own words are the FIRST text part. Later text parts are org policy and agent knowledge:
  system instructions, never changes.
- With chat history the first part is "{history}\\n\\nCurrent message: {text}"; the text after the last marker
  is this turn's message and the history before it is parsed into turns (`history.py`): this agent's only
  memory, since nothing is stored between messages.
- Version and date are taken only from a header line or a list line starting with "Release"; never guessed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from google.protobuf.json_format import MessageToDict

import history
from history import Turn

AUDIENCES = ("all", "admins", "developers", "end users")
TONES = ("neutral", "friendly", "formal")

_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_KEYS = r"version|released?|release date|date|product|audience|tone|channel|link|notes link|full notes"
_SEGMENT_SPLIT = re.compile(rf"\s*[;,]\s*(?=(?:{_KEYS})\b)", re.IGNORECASE)
_VERSION_VALUE = r"v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.]+)?"
_HEADER = {
    "version": re.compile(rf"^version\s*[:=]?\s*({_VERSION_VALUE})\.?$", re.IGNORECASE),
    "date": re.compile(r"^(?:released?(?: on)?|release date|date)\s*[:=]?\s*(\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.? "
                       r"\d{1,2}(?:,? \d{4})?|\d{1,2} [A-Za-z]{3,9}\.?(?: \d{4})?)$", re.IGNORECASE),
    "product": re.compile(r"^product\s*[:=]?\s*(\S.{0,60})$", re.IGNORECASE),
    "audience": re.compile(r"^audience\s*[:=]?\s*(admins?|developers?|end[ -]users?|all)\.?$", re.IGNORECASE),
    "tone": re.compile(r"^tone\s*[:=]?\s*(friendly|neutral|formal)\.?$", re.IGNORECASE),
    "channel": re.compile(r"^channel\s*[:=]?\s*(#?[\w-]{1,80})$", re.IGNORECASE),
    "link": re.compile(r"^(?:link|notes link|full notes)\s*[:=]?\s*(https?://\S+)$", re.IGNORECASE),
}
_RELEASE_LINE = re.compile(r"^\s*(?:#+\s*)?release\b", re.IGNORECASE)
_VERSION_IN = re.compile(rf"\b{_VERSION_VALUE}\b")
_DATE_IN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_ASK_VERB = re.compile(r"\b(?:write|draft|make|create|generate|turn|prepare|produce|need)\b", re.IGNORECASE)
_INSTRUCTION = re.compile(r"release notes|changelog|announcement|these (?:merged )?(?:prs|pull requests|changes|"
                          r"commits|issues)|from (?:these|this|the following)", re.IGNORECASE)
_INCLUDE_INTERNAL = re.compile(r"\binclude (?:the )?internal\b", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    """One turn's input: the user's words, platform policy parts, whether JSON output was asked, and the earlier
    turns the platform sent along (oldest first; empty when there are none)."""

    text: str
    policy: tuple[str, ...] = ()
    wants_json: bool = False
    history: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class Header:
    version: str = ""
    date: str = ""
    product: str = ""
    audience: str = "all"
    tone: str = "neutral"
    channel: str = ""
    link: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> Header:
        data = data or {}
        values = {k: str(data.get(k) or "") for k in ("version", "date", "product", "channel", "link")}
        audience = data.get("audience") if data.get("audience") in AUDIENCES else "all"
        tone = data.get("tone") if data.get("tone") in TONES else "neutral"
        return Header(**values, audience=audience, tone=tone)

    def text(self) -> str:
        return " ".join(v for v in (self.version, self.date, self.product, self.channel, self.link) if v)


@dataclass(frozen=True)
class Split:
    header: Header
    instruction: str      # a leading request line ("Write release notes from these merged PRs:")
    body: str             # the change list
    include_internal: bool = False


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    block, user = history.split(texts[0]) if texts else ("", "")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(user))
    return Incoming(user, tuple(t for t in texts[1:] if t.strip()), wants, history.parse(block))


def normalise_audience(value: str) -> str:
    v = value.casefold().replace("-", " ")
    if v.startswith("admin"):
        return "admins"
    if v.startswith("developer"):
        return "developers"
    return "end users" if v.startswith("end") else "all"


def header_values(line: str) -> dict[str, str] | None:
    """The header values on one line, or None when any part of the line is not a header entry."""
    found: dict[str, str] = {}
    for segment in _SEGMENT_SPLIT.split(line.strip().strip(".")):
        segment = segment.strip()
        for key, pattern in _HEADER.items():
            match = pattern.match(segment)
            if match:
                found[key] = match.group(1).strip()
                break
        else:
            return None
    return found or None


def apply_values(header: Header, values: dict[str, str]) -> Header:
    updates = dict(values)
    if "audience" in updates:
        updates["audience"] = normalise_audience(updates["audience"])
    if "tone" in updates:
        updates["tone"] = updates["tone"].casefold()
    if "channel" in updates and not updates["channel"].startswith("#"):
        updates["channel"] = "#" + updates["channel"]
    return replace(header, **updates)


def is_instruction(line: str) -> bool:
    """A request line ("Write release notes from these merged PRs:"), not a change."""
    return len(line) <= 300 and bool(_ASK_VERB.search(line)) and bool(_INSTRUCTION.search(line))


def split_message(text: str) -> Split:
    """Header lines and one request line at the top; then the change list. A list line starting with
    "Release" gives the version and date when the header has none; it is not a change."""
    lines = (text or "").splitlines()
    header, instruction, start = Header(), "", 0
    while start < len(lines):
        line = lines[start].strip()
        values = header_values(line) if line else {}
        if values is None and not instruction and is_instruction(line):
            instruction = line
        elif values is None:
            break
        header = apply_values(header, values or {})
        start += 1
    body: list[str] = []
    for line in lines[start:]:
        if _RELEASE_LINE.match(line) and (_VERSION_IN.search(line) or _DATE_IN.search(line)):
            version, date = _VERSION_IN.search(line), _DATE_IN.search(line)
            header = replace(header, version=header.version or (version.group(0) if version else ""),
                             date=header.date or (date.group(0) if date else ""))
            continue
        body.append(line)
    return Split(header, instruction, "\n".join(body).strip(), bool(_INCLUDE_INTERNAL.search(instruction)))
