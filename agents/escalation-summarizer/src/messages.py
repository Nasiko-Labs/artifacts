"""Pure: split a pasted thread into messages m1..mn with speaker and timestamp, and drop quoted copies.

Header styles recognised at a line start:
    Name (2026-11-03 14:20): text          [10:42] Name: text
    From: Name ... Sent: / Date: <when>    On <date>, <name> wrote:
Lines starting with ">" are quoted copies of earlier mail and are not part of a message's own text. A
message whose own text is empty, or is contained in an earlier message, is marked `quoted` and skipped.
A thread with no recognised header is one message with no speaker (attribution is then not available).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import grounding as g

_NAME = r"[A-Za-z][\w.'’-]*(?: [A-Za-z][\w.'’-]*){0,4}"
_PAREN = re.compile(rf"^\s*\**(?P<speaker>{_NAME})\**\s*\((?P<ts>[^()\n]{{4,40}})\)\s*:\s*(?P<body>.*)$")
_BRACKET = re.compile(rf"^\s*\[(?P<ts>[^\]\n]{{3,40}})\]\s*(?P<speaker>{_NAME})\s*:\s*(?P<body>.*)$")
_FROM = re.compile(r"^\s*From:\s*(?P<speaker>[^\n<]{1,80}?)\s*(?:<[^>\n]*>)?\s*$", re.IGNORECASE)
_WROTE = re.compile(r"^\s*On (?P<ts>.{4,60}?),\s*(?P<speaker>[^,\n]{1,60}?)\s*(?:<[^>\n]*>)?\s*wrote:\s*$")
_SENT = re.compile(r"^\s*(?:Sent|Date):\s*(?P<ts>.+?)\s*$", re.IGNORECASE)
_MAIL_HEADER = re.compile(r"^\s*(?:To|Cc|Bcc|Subject|Reply-To):", re.IGNORECASE)
_QUOTED_LINE = re.compile(r"^\s*>")


@dataclass(frozen=True)
class Message:
    id: str
    speaker: str
    timestamp: str
    body: str
    quoted: bool = False


def is_header(line: str) -> bool:
    return any(p.match(line) for p in (_PAREN, _BRACKET, _FROM, _WROTE))


def parse(thread: str) -> tuple[Message, ...]:
    """Every message in order, quoted copies included (marked), with ids m1..mn over the kept ones."""
    raw = _split(thread.replace("\r\n", "\n"))
    kept: list[Message] = []
    seen: list[str] = []
    for speaker, ts, lines in raw:
        body = "\n".join(line for line in lines if not _QUOTED_LINE.match(line)).strip()
        norm = g.normalise(body)
        quoted = not norm or any(norm in earlier for earlier in seen)
        if not quoted:
            seen.append(norm)
        kept.append(Message("", speaker, ts, body, quoted))
    numbered, n = [], 0
    for message in kept:
        if not message.quoted:
            n += 1
        numbered.append(Message(f"m{n}" if not message.quoted else "", message.speaker, message.timestamp,
                                message.body, message.quoted))
    return tuple(numbered)


def _split(text: str) -> list[tuple[str, str, list[str]]]:
    messages: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] = ("", "", [])
    in_mail_header = False
    for line in text.split("\n"):
        start = _start(line)
        if start is not None:
            if current[2] or current[0]:
                messages.append(current)
            speaker, ts, first = start
            current = (speaker, ts, [first] if first else [])
            in_mail_header = bool(_FROM.match(line))
            continue
        if in_mail_header and (_SENT.match(line) or _MAIL_HEADER.match(line)):
            sent = _SENT.match(line)
            current = (current[0], sent.group("ts") if sent and not current[1] else current[1], current[2])
            continue
        in_mail_header = in_mail_header and not line.strip()
        current[2].append(line)
    if current[2] or current[0]:
        messages.append(current)
    return messages


def _start(line: str) -> tuple[str, str, str] | None:
    for pattern in (_PAREN, _BRACKET, _WROTE):
        match = pattern.match(line)
        if match:
            body = match.groupdict().get("body") or ""
            return match.group("speaker").strip(" *"), match.group("ts").strip(), body.strip()
    match = _FROM.match(line)
    return (match.group("speaker").strip(" \"'"), "", "") if match else None


def own(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """The messages that carry their own text (quoted copies left out)."""
    return tuple(m for m in messages if not m.quoted)


def labelled(messages: tuple[Message, ...]) -> str:
    """The thread as the model sees it: each own message under its id, speaker and time."""
    blocks = []
    for m in own(messages):
        who = m.speaker or "Unknown"
        when = f" ({m.timestamp})" if m.timestamp else ""
        blocks.append(f"[{m.id}] {who}{when}:\n{m.body}")
    return "\n\n".join(blocks)


def locate_message(snippet: str, messages: tuple[Message, ...], hint: str | None) -> Message | None:
    """The own message holding the snippet: the hinted one if it does, else the first that does."""
    holding = [m for m in own(messages) if g.locate(snippet, g.normalise(m.body)) is not None]
    for m in holding:
        if m.id == hint:
            return m
    return holding[0] if holding else None


def index_of(message_id: str | None) -> int:
    return int(message_id[1:]) if message_id and message_id[1:].isdigit() else 0


def to_state(messages: tuple[Message, ...]) -> list[dict]:
    return [asdict(m) for m in messages]


def from_state(data: Any) -> tuple[Message, ...]:
    return tuple(Message(**{k: (bool(v) if k == "quoted" else str(v or "")) for k, v in dict(d).items()
                            if k in Message.__dataclass_fields__}) for d in data or ())
