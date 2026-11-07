"""Pure: split a pasted text into messages, in code.

A new message starts at an email header group (`From:`/`To:`/`Subject:`/`Date:`), a chat line (`[10:32]
Name: ...`, `Name: ...`, a `Name  10:32 AM` line), or a dated note bullet (`- 2026-11-03: ...`). Without
email headers, a blank line also ends a message. Each message keeps its speaker, recipient, sent day and
its own text; quotes are checked against that text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

import dates

MAX_MESSAGE_CHARS = 12_000
MIN_SPEAKER_LINES = 2

_HEADER = re.compile(r"^\s*(from|to|cc|subject|date|sent)\s*:\s*(.*)$", re.IGNORECASE)
_NAME = r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}"
_SPEAKER = re.compile(rf"^\s*(?:\[[^\]]{{1,25}}\]\s*)?({_NAME})\s*(?:\([^)]{{1,25}}\))?\s*:\s+\S")
_CHAT_STAMP = re.compile(rf"^\s*({_NAME})\s{{1,4}}\[?\d{{1,2}}:\d{{2}}(?:\s?[AaPp][Mm])?\]?\s*$")
_BULLET = re.compile(r"^\s*[-*•]\s*(\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2}|\d{1,2} [A-Z][a-z]{2,8})\s*[:\-–]")
_LEADING_DATE = re.compile(r"^\s*[-*•\[]?\s*(\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.? \d{1,2}\b|\d{1,2} [A-Za-z]{3,9}\b)")
_NOT_SPEAKERS = frozenset({"note", "notes", "action", "actions", "todo", "re", "fwd", "agenda", "decision",
                           "decisions", "update", "updates", "next steps", "subject", "summary", "context",
                           "attendees", "topic", "fyi", "ps", "p.s", "q", "a", "eta", "status"})
_TO_FIRST_LINE = re.compile(r"(?:@([A-Za-z][\w.-]*)|\bto\s+([A-Z][\w-]*))")
_ADDRESS = re.compile(r"<[^>]*>")


@dataclass(frozen=True)
class Message:
    id: str
    text: str
    speaker: str = ""
    to: str = ""
    sent_on: str = ""        # YYYY-MM-DD or ""
    addressing: str = ""     # header lines + first body line: where a recipient may be written


def split(source: str, as_of: date) -> tuple[Message, ...]:
    lines = (source or "").replace("\r\n", "\n").split("\n")
    email = any(_HEADER.match(line) for line in lines)
    chat = sum(1 for line in lines if _speaker_of(line)) >= MIN_SPEAKER_LINES
    blocks: list[list[str]] = []
    current: list[str] = []
    previous_header = False
    for line in lines:
        is_header = bool(_HEADER.match(line))
        if not line.strip() and not email:
            current = _close(blocks, current)
            continue
        starts = (is_header and not previous_header) or bool(_BULLET.match(line)) or bool(_CHAT_STAMP.match(line))
        starts = starts or (chat and not email and bool(_speaker_of(line)))
        if starts:
            current = _close(blocks, current)
        current.append(line)
        previous_header = is_header
    _close(blocks, current)
    built = [_message(block, as_of) for block in blocks]
    return _numbered([piece for message in built for piece in _pieces(message)])


def _close(blocks: list[list[str]], current: list[str]) -> list[str]:
    if any(line.strip() for line in current):
        blocks.append(current)
    return []


def _speaker_of(line: str) -> str:
    if _HEADER.match(line):
        return ""
    match = _SPEAKER.match(line) or _CHAT_STAMP.match(line)
    if not match or match.group(1).casefold() in _NOT_SPEAKERS:
        return ""
    return match.group(1).strip()


def _message(block: list[str], as_of: date) -> Message:
    headers = {}
    for line in block:
        match = _HEADER.match(line)
        if match:
            headers.setdefault(match.group(1).casefold(), match.group(2).strip())
    body = [line for line in block if not _HEADER.match(line) and line.strip()]
    first = body[0] if body else ""
    speaker = _clean_name(headers.get("from", "")) or _speaker_of(block[0])
    to = _clean_name(headers.get("to", "")) or _first_line_to(first)
    dated = headers.get("date") or headers.get("sent") or ""
    sent = dates.explicit_sent(dated, as_of) if dated else _leading_date(first or block[0], as_of)
    header_lines = [line.strip() for line in block if _HEADER.match(line)]
    return Message(id="", text="\n".join(block).strip(), speaker=speaker, to=to,
                   sent_on=sent.isoformat() if sent else "", addressing="\n".join([*header_lines, first]))


def _clean_name(value: str) -> str:
    first = value.split(",")[0].split(";")[0]
    name = _ADDRESS.sub("", first).strip().strip('"')
    return name or first.strip().strip("<>")


def _first_line_to(line: str) -> str:
    match = _TO_FIRST_LINE.search(line)
    return (match.group(1) or match.group(2)) if match else ""


def _leading_date(line: str, as_of: date) -> date | None:
    match = _LEADING_DATE.match(line)
    return dates.explicit_sent(match.group(1), as_of) if match else None


def _pieces(message: Message) -> list[Message]:
    """An over-long message is cut into pieces on line breaks; each keeps the message's metadata."""
    if len(message.text) <= MAX_MESSAGE_CHARS:
        return [message]
    pieces, rest = [], message.text
    while rest:
        cut = len(rest) if len(rest) <= MAX_MESSAGE_CHARS else (rest.rfind("\n", 0, MAX_MESSAGE_CHARS) + 1
                                                                 or MAX_MESSAGE_CHARS)
        pieces.append(replace(message, text=rest[:cut].strip()))
        rest = rest[cut:]
    return [p for p in pieces if p.text]


def _numbered(messages: Sequence[Message]) -> tuple[Message, ...]:
    return tuple(replace(m, id=f"m{n}") for n, m in enumerate(messages, start=1))


def speakers(messages: Sequence[Message]) -> tuple[str, ...]:
    """Distinct speakers, in order of first appearance (case-insensitive)."""
    seen: dict[str, str] = {}
    for message in messages:
        if message.speaker and message.speaker.casefold() not in seen:
            seen[message.speaker.casefold()] = message.speaker
    return tuple(seen.values())


def by_id(messages: Sequence[Message]) -> dict[str, Message]:
    return {m.id: m for m in messages}


def to_state(messages: Sequence[Message]) -> list[dict]:
    return [m.__dict__.copy() for m in messages]


def from_state(raw) -> tuple[Message, ...]:
    names = Message.__dataclass_fields__
    return tuple(Message(**{k: str(v or "") for k, v in dict(d).items() if k in names}) for d in raw or ())


FIRST_PERSON_FUTURE = re.compile(r"\bI(?:['’]ll| will| can| am going to|['’]m going to)\b")


def first_person_count(text: str) -> int:
    return len(FIRST_PERSON_FUTURE.findall(text or ""))


def whose(messages: Sequence[Message], my_name: str = "") -> tuple[tuple[Message, ...], str]:
    """(the user's messages, the speaker guessed as the user or ""). With `my_name`, that speaker's messages
    (and unlabelled ones); without it and with several speakers, the one with the most first-person future
    statements ("I'll", "I will", "I can", "I'm going to"); a tie goes to the first speaker."""
    names = speakers(messages)
    if my_name:
        wanted = my_name.casefold()
        matched = [n for n in names if n.casefold() == wanted or n.casefold().split()[0] == wanted.split()[0]]
        if matched or not names:
            return tuple(m for m in messages if not m.speaker or m.speaker in matched), ""
    if len(names) < 2:
        return tuple(messages), ""
    counts = {n: sum(first_person_count(m.text) for m in messages if m.speaker == n) for n in names}
    best = max(names, key=lambda n: counts[n])  # max keeps the first of equal counts
    return tuple(m for m in messages if not m.speaker or m.speaker == best), best
