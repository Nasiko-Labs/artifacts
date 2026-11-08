"""Pure: what the user gave this turn, parsed in code.

- The user's words are the FIRST text part; later parts are org policy and agent knowledge (never request
  or evidence). With chat history only the text after the last "Current message:" marker is used.
- The request is the first paragraph (plus a short closing question); the rest of the message is pasted
  evidence. Window (default 30, clamped 7-90), angle (build / invest-time / watch), named repositories,
  follow-up commands and "as json" are read here, never by the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
DEFAULT_WINDOW, MIN_WINDOW, MAX_WINDOW = 30, 7, 90
MAX_TOPIC_CHARS = 300
MAX_REQUEST_CHARS = 600
MAX_CLOSING_CHARS = 200
MAX_REPOS = 2

_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_UNITS = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}
_WORD_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "few": 3, "couple of": 2}
_WINDOW = re.compile(r"\b(?:last|past|previous|over the last)\s+(\d{1,3}|a|one|two|three|four|six|few|couple of)?\s*"
                     r"(day|week|month|quarter|year)s?\b", re.IGNORECASE)
_ANGLES = (("watch", re.compile(r"\bthreats?\b|\bwhat to watch\b|\bwatch (?:out|for)\b|\bwatch angle\b", re.IGNORECASE)),
           ("invest-time", re.compile(r"\bwhat to (?:learn|adopt)\b|\bworth (?:learning|adopting)\b|"
                                      r"\binvest(?:ing)? (?:my |our )?time\b|\binvest-time\b", re.IGNORECASE)))
_URL_START = re.compile(r"^\s*(?:[-*•]\s*)?https?://", re.IGNORECASE)
_REPO = re.compile(r"(?<![\w/:.@-])([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9][\w.-]{0,99})(?![\w/])")
_NOT_REPOS = {"and/or", "n/a", "w/o", "either/or", "on/off", "yes/no", "tcp/ip", "i/o", "ci/cd"}
_TOPIC_HINT = re.compile(r"\b(?:brief|report|signals?|opportunit(?:y|ies)|trends?|trending|landscape)\s+"
                         r"(?:on|in|for|about|around|of)\s+(.{3,300})", re.IGNORECASE)
_WANTS_HN = re.compile(r"hacker ?news|\bhn\b", re.IGNORECASE)
_WANTS_CODE = re.compile(r"\brepos?\b|repositor|code host|open[- ]source", re.IGNORECASE)


@dataclass(frozen=True)
class Incoming:
    text: str
    wants_json: bool = False


@dataclass(frozen=True)
class Request:
    request: str            # the ask, without pasted material
    pasted: str             # everything else
    window_days: int
    window_note: str
    angle: str
    repos: tuple[str, ...]
    wants_hn: bool
    wants_code: bool


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(CURRENT_MESSAGE_MARKER):]


def read_message(message: Any) -> Incoming:
    if message is None:
        return Incoming("")
    texts = [p.text for p in message.parts if p.WhichOneof("content") == "text"]
    user = current_message(texts[0]) if texts else ""
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    wants = str(metadata.get("output") or "").casefold() == "json" or bool(_JSON.search(user))
    return Incoming(user.strip(), wants)


def parse(text: str) -> Request:
    request, pasted = split(text)
    days, note = window(request)
    return Request(request=request, pasted=pasted, window_days=days, window_note=note, angle=angle(request),
                   repos=repos(request), wants_hn=bool(_WANTS_HN.search(request)),
                   wants_code=bool(_WANTS_CODE.search(request)))


def split(text: str) -> tuple[str, str]:
    """(request, pasted). One paragraph = all request. Otherwise the first paragraph is the request (unless it
    starts with a URL) and a short closing question is appended to it."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", (text or "").strip()) if b.strip()]
    if len(blocks) <= 1:
        lines = (blocks[0] if blocks else "").splitlines()
        plain = [n for n, ln in enumerate(lines) if not _URL_START.match(ln)]
        if len(plain) == len(lines) or not plain:
            return (blocks[0] if blocks else ""), ""
        return lines[plain[0]], "\n".join(ln for n, ln in enumerate(lines) if n != plain[0])
    request: list[str] = []
    if not _URL_START.match(blocks[0]) and len(blocks[0]) <= MAX_REQUEST_CHARS:
        request.append(blocks.pop(0))
    last = blocks[-1] if blocks else ""
    if last.endswith("?") and len(last) <= MAX_CLOSING_CHARS and "http" not in last and len(blocks) > 1:
        request.append(blocks.pop())
    return " ".join(request), "\n\n".join(blocks)


def window(text: str) -> tuple[int, str]:
    match = _WINDOW.search(text or "")
    if not match:
        return DEFAULT_WINDOW, ""
    count_raw = (match.group(1) or "1").casefold()
    count = int(count_raw) if count_raw.isdigit() else _WORD_NUMBERS.get(count_raw, 1)
    days = count * _UNITS[match.group(2).casefold()]
    clamped = min(MAX_WINDOW, max(MIN_WINDOW, days))
    note = "" if clamped == days else f"Window set to {clamped} days (I read {MIN_WINDOW}-{MAX_WINDOW}; you asked for {days})."
    return clamped, note


def angle(text: str) -> str:
    return next((name for name, pattern in _ANGLES if pattern.search(text or "")), "build")


def repos(text: str) -> tuple[str, ...]:
    no_urls = re.sub(r"https?://\S+", " ", text or "")
    found = [f"{o}/{n.rstrip('.')}" for o, n in _REPO.findall(no_urls)
             if f"{o}/{n}".casefold() not in _NOT_REPOS and not o.isdigit()]
    return tuple(dict.fromkeys(found))[:MAX_REPOS]


def topic_hint(request: str) -> str:
    """A topic named in the request's own words ("brief on X", "trending in X"), or "" when none is."""
    match = _TOPIC_HINT.search(request or "")
    if not match:
        return ""
    words = _WINDOW.sub(" ", match.group(1))
    words = re.split(r"[.?!;]|,\s*(?:focus|last|past|only|as json)", words, maxsplit=1)[0]
    return clean_topic(words)


def clean_topic(topic: str) -> str:
    text = " ".join(str(topic or "").split()).strip(" .,?!:;\"'")
    return text[:MAX_TOPIC_CHARS]
