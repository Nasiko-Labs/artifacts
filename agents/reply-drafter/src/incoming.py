"""Pure: reading the incoming message itself. Email headers (sender, subject), the sender's first name as
found on a sign-off line, the `Re:` subject, and a previous agent's envelope given as the message."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

MAX_INCOMING_CHARS = 6_000
ENVELOPE_FIELDS = ("text", "body", "draft")

_FROM = re.compile(r"^\s*from:\s*(.{1,160})$", re.IGNORECASE | re.MULTILINE)
_SUBJECT = re.compile(r"^\s*subject:\s*(.{1,200})$", re.IGNORECASE | re.MULTILINE)
_HEADER = re.compile(r"^\s*(?:from|to|cc|subject|date|sent):", re.IGNORECASE | re.MULTILINE)
_RE_PREFIX = re.compile(r"^(?:(?:re|fwd?|aw)\s*:\s*)+", re.IGNORECASE)
_CLOSER_NAME = re.compile(r"^(?:thanks|thank you|many thanks|best|best regards|kind regards|regards|cheers|"
                          r"sincerely|warmly|talk soon|take care|thx)[,!.]?\s*[-–]?\s*([A-Z][a-z'’-]+)\b.{0,20}$",
                          re.IGNORECASE)
_NAME_LINE = re.compile(r"^[-–—~]?\s*([A-Z][a-z'’-]+)(?:\s+[A-Z][a-z'’-]+)?\.?$")
_JSON_START = re.compile(r"^\s*(?:```(?:json)?\s*)?\{", re.MULTILINE)
_FENCE_END = re.compile(r"\s*```\s*$")


@dataclass(frozen=True)
class Incoming:
    text: str
    sender: str = ""          # the sender's first name, when the message shows it
    subject: str = ""         # the incoming subject line, without any Re:/Fwd: prefix
    is_email: bool = False    # it carries email headers
    truncated: bool = False

    @property
    def reply_subject(self) -> str:
        return f"Re: {self.subject}" if self.subject else ""


def read_incoming(text: str, cue_sender: str = "") -> Incoming:
    raw = (text or "").strip()
    capped = raw[:MAX_INCOMING_CHARS]
    subject = _SUBJECT.search(capped)
    sender = _from_header(capped) or _first_name(cue_sender) or _from_sign_off(capped)
    return Incoming(text=capped, sender=sender,
                    subject=_RE_PREFIX.sub("", subject.group(1).strip()) if subject else "",
                    is_email=bool(_FROM.search(capped) or subject), truncated=len(raw) > MAX_INCOMING_CHARS)


def has_headers(text: str) -> bool:
    return bool(_FROM.search(text or ""))


def header_start(text: str) -> int:
    """Where the first email header line starts (for text pasted without a cue)."""
    match = _HEADER.search(text or "")
    return match.start() if match else -1


def _from_header(text: str) -> str:
    match = _FROM.search(text)
    if match is None:
        return ""
    value = match.group(1).strip().strip('"')
    name = value.split("<", 1)[0].strip().strip('"')
    if not name and "@" in value:
        name = re.sub(r"[<>]", "", value).split("@", 1)[0].split(".")[0].capitalize()
    return _first_name(name)


def _first_name(name: str) -> str:
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    if "," in (name or ""):  # "Lee, Dana" -> Dana
        first = name.split(",", 1)[1].strip().split()[0] if name.split(",", 1)[1].strip() else first
    first = first.strip(".,;:'\"")
    return first if first[:1].isupper() and first.replace("-", "").replace("'", "").isalpha() else ""


def _from_sign_off(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        tail = re.split(r"(?<=[.!?])\s+", text.strip())
        lines = tail if len(tail) >= 2 else []
    for line in reversed(lines[-2:]):
        closer = _CLOSER_NAME.match(line)
        if closer:
            return _first_name(closer.group(1))
        named = _NAME_LINE.match(line)
        if named and line is lines[-1]:
            return _first_name(named.group(1))
    return ""


def envelope_text(text: str) -> tuple[str, str] | None:
    """(the text before it, the message) when the message is given as a previous agent's JSON envelope
    with a `text`, `body` or `draft` field; else None."""
    for match in _JSON_START.finditer(text or ""):
        blob = _FENCE_END.sub("", text[match.start():].strip())
        blob = re.sub(r"^```(?:json)?\s*", "", blob)
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for key in ENVELOPE_FIELDS:
            if isinstance(data.get(key), str) and data[key].strip():
                return text[:match.start()].strip(), data[key].strip()
    return None
