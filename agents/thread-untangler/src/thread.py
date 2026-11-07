"""Pure: split a pasted thread into numbered messages M1..Mn with speakers.

Formats are tried in order and the first that yields at least MIN_MESSAGES messages wins:
email (`From:` headers; an `On ..., X wrote:` block and `>` lines are quoted context attached to the
message that quoted them, never new messages), Slack copy-paste (a name with a time), labelled lines
(`Name: text`), and as a fallback paragraphs from speaker `Unknown`. Emoji-only and "joined the channel"
lines are kept as messages but flagged `noise` and never sent to the model.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from names import is_name, norm_name

MIN_MESSAGES = 3
UNKNOWN = "Unknown"
NOISE_MAX_WORDS = 12

_TIME = r"\d{1,2}:\d{2}(?:\s?[AaPp]\.?[Mm]\.?)?"
_NAME = r"[A-Za-z@][\w@.'’ -]{0,39}?"
_SLACK_HEADERS = (
    re.compile(rf"^\s*(?P<name>{_NAME})\s+(?P<time>{_TIME})\s*$"),                       # Name  10:32 AM
    re.compile(rf"^\s*(?P<name>{_NAME})\s*\[(?P<time>{_TIME})\]\s*:?\s*(?P<text>.*)$"),   # Name [10:32] text
    re.compile(rf"^\s*\[(?P<time>{_TIME})\]\s*(?P<name>{_NAME})\s*:\s*(?P<text>.*)$"),    # [10:32] Name: text
)
_LABELLED = re.compile(rf"^\s*(?P<name>{_NAME})\s*:(?:\s+(?P<text>.*))?$")
_EMAIL_FROM = re.compile(r"^\s*From:\s*(?P<who>.+?)\s*$", re.IGNORECASE)
_EMAIL_WHEN = re.compile(r"^\s*(?:Date|Sent):\s*(?P<when>.+?)\s*$", re.IGNORECASE)
_EMAIL_OTHER = re.compile(r"^\s*(?:To|Cc|Bcc|Subject|Reply-To):", re.IGNORECASE)
_WROTE = re.compile(r"^\s*On\s.{3,240}?\bwrote:\s*$", re.IGNORECASE)
_QUOTE_LINE = re.compile(r"^\s*>")
_ADDRESS = re.compile(r"\s*<[^>]*>\s*")
_NOISE = re.compile(r"\b(?:has )?(?:joined|left) (?:the )?(?:channel|conversation|#\S+)|\bwas added to (?:the )?"
                    r"(?:channel|#\S+)|\bset the channel (?:topic|description)", re.IGNORECASE)
_EMOJI_CODE = re.compile(r":[a-z0-9_+-]+:", re.IGNORECASE)
_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Message:
    id: str
    speaker: str
    time_label: str
    text: str
    noise: bool = False
    quoted: str = ""


@dataclass(frozen=True)
class Thread:
    messages: tuple[Message, ...]
    fmt: str  # email | slack | labelled | paragraphs

    def index(self, message_id: str) -> int | None:
        match = re.fullmatch(r"M(\d{1,5})", (message_id or "").strip().upper())
        number = int(match.group(1)) if match else 0
        return number - 1 if 0 < number <= len(self.messages) else None

    def get(self, message_id: str) -> Message | None:
        at = self.index(message_id)
        return None if at is None else self.messages[at]

    @property
    def content(self) -> tuple[Message, ...]:
        return tuple(m for m in self.messages if not m.noise)

    @property
    def speakers(self) -> tuple[str, ...]:
        seen: dict[str, str] = {}
        for m in self.content:
            if m.speaker != UNKNOWN:
                seen.setdefault(norm_name(m.speaker), m.speaker)
        return tuple(seen.values())

    def to_state(self) -> dict:
        return {"fmt": self.fmt, "messages": [asdict(m) for m in self.messages]}

    @staticmethod
    def from_state(data: Mapping | None) -> Thread | None:
        if not data or not data.get("messages"):
            return None
        fields = Message.__dataclass_fields__
        messages = tuple(Message(**{k: v for k, v in dict(m).items() if k in fields}) for m in data["messages"])
        return Thread(messages, str(data.get("fmt") or "paragraphs"))


def parse(text: str) -> Thread:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for fmt, parser in (("email", _email), ("slack", _slack), ("labelled", _labelled)):
        thread = _number(parser(lines), fmt)
        if len(thread.content) >= MIN_MESSAGES:
            return thread
    return _number(_paragraphs(text or ""), "paragraphs")


def _number(raw: Sequence[tuple[str, str, str, str]], fmt: str) -> Thread:
    messages = []
    for speaker, when, body, quoted in raw:
        text = body.strip()
        if not text and not quoted.strip():
            continue
        messages.append(Message(f"M{len(messages) + 1}", speaker.strip() or UNKNOWN, when.strip(), text,
                                is_noise(text), quoted.strip()))
    return Thread(tuple(messages), fmt)


def is_noise(text: str) -> bool:
    """Empty, a join/leave line, or only emoji and punctuation."""
    if not text.strip() or (_NOISE.search(text) and len(text.split()) <= NOISE_MAX_WORDS):
        return True
    return not any(ch.isalnum() for ch in _EMOJI_CODE.sub("", text))


@dataclass
class _Email:
    """One email while it is being read (local to `_email`)."""

    speaker: str
    when: str = ""
    body: list[str] = field(default_factory=list)
    quoted: list[str] = field(default_factory=list)
    in_headers: bool = True
    in_quote: bool = False


def _email(lines: Sequence[str]) -> list[tuple[str, str, str, str]]:
    found: list[_Email] = []
    for line in lines:
        start = _EMAIL_FROM.match(line)
        if start or not found:
            found += [_Email(_sender(start.group("who")))] if start else []
            continue
        current = found[-1]
        when = _EMAIL_WHEN.match(line) if current.in_headers else None
        if when or (current.in_headers and _EMAIL_OTHER.match(line)):
            current.when = when.group("when") if when else current.when
            continue
        current.in_headers = False
        current.in_quote = current.in_quote or bool(_WROTE.match(line))
        (current.quoted if current.in_quote or _QUOTE_LINE.match(line) else current.body).append(line)
    return [(e.speaker, e.when, "\n".join(e.body), "\n".join(e.quoted)) for e in found]


def _sender(who: str) -> str:
    name = _ADDRESS.sub(" ", who).strip().strip('"\'')
    return name or who.strip("<> ")


def _slack(lines: Sequence[str]) -> list[tuple[str, str, str, str]]:
    def header(line: str):
        for pattern in _SLACK_HEADERS:
            match = pattern.match(line)
            if match and is_name(match.group("name")):
                return match.group("name"), match.group("time"), match.groupdict().get("text") or ""
        return None
    return _by_headers(lines, header)


def _labelled(lines: Sequence[str]) -> list[tuple[str, str, str, str]]:
    def header(line: str):
        match = _LABELLED.match(line)
        if match and is_name(match.group("name")):
            return match.group("name"), "", match.group("text") or ""
        return None
    return _by_headers(lines, header)


def _by_headers(lines: Sequence[str], header: Callable) -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, list[str]]] = []
    for line in lines:
        start = header(line)
        if line.strip() and is_noise(line):  # a reaction or a join line is its own (noise) message
            found.append((UNKNOWN, "", [line]))
        elif start is not None:
            found.append((start[0], start[1], [start[2]] if start[2] else []))
        elif found:
            found[-1][2].append(line)
    return [(name, when, "\n".join(body), "") for name, when, body in found]


def _paragraphs(text: str) -> list[tuple[str, str, str, str]]:
    return [(UNKNOWN, "", p, "") for p in _PARAGRAPH.split(text) if p.strip()]
