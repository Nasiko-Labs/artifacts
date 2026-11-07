"""Pure: the user's message read in code. Scope (`in #infra`), window (months, clamped 1-36), json
mode, a write ask (stripped and refused, never acted on), follow-ups, an envelope as input, and the
cut of a long paste used to build the searches. No model call happens here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

DEFAULT_MONTHS = 12
MIN_MONTHS, MAX_MONTHS = 1, 36
QUERY_TEXT_CHARS = 1500
MIN_QUESTION_WORDS = 3

FollowKind = Literal["more", "expand", "widen", "rescope"]

_JSON = re.compile(r"\b(?:as|in)\s+json\b|\bjson\s+only\b", re.IGNORECASE)
_CHANNEL = re.compile(r"#([a-z0-9][a-z0-9_.-]{0,79})", re.IGNORECASE)
_SCOPE_PHRASE = re.compile(
    r"\b(?:(?:search|look|check)\s+)?(?:(?:only\s+)?(?:in|from|within|on)\s+)?(?:the\s+)?"
    r"#[\w.-]+(?:\s*(?:,|and|or)\s*#[\w.-]+)*(?:\s+(?:channel|channels))?(?:\s+only)?",
    re.IGNORECASE)
_MONTHS = re.compile(r"\b(?:(?:in\s+)?(?:the\s+)?(?:last|past)\s+)?(\d{1,3})\s+months?\b", re.IGNORECASE)
_YEARS = re.compile(r"\b(?:(?:in\s+)?(?:the\s+)?(?:last|past)\s+)?(\d{1,2})\s+years?\b", re.IGNORECASE)
_LAST_YEAR = re.compile(r"\b(?:in\s+)?the\s+(?:last|past)\s+year\b", re.IGNORECASE)
_WRITE = re.compile(r"[,;]?\s*(?:(?:and|then|if not|otherwise|or)[,]?\s+)?(?:please\s+)?\b(?:post|send|share|forward|"
                    r"cross-?post|put)\s+(?:my|the|this|it)\b[^.?!]*[.?!]?", re.IGNORECASE)
_MORE = re.compile(r"^\s*(?:more|show more|more results|next|show the rest)\s*[.!?]*\s*$", re.IGNORECASE)
_EXPAND = re.compile(r"^\s*(?:expand\s*:?\s*#?(\d+)|(?:show|open|see)\s+(?:me\s+)?(?:the\s+)?(?:whole\s+|full\s+)?"
                     r"thread\s*(?:of\s+|for\s+)?(?:#|no\.?\s*|number\s+|answer\s+)?(\d+))\s*[.!?]*\s*$", re.IGNORECASE)
_WIDEN = re.compile(r"^\s*(?:widen\s*:?\s*(\d+)|(?:widen|search|look|go back|extend)\b.*?\b(\d{1,3})\s+months?|"
                    r"(?:widen|search|look|go back|extend)\b.*?\b(\d{1,2})\s+years?)\b", re.IGNORECASE)
_SPACES = re.compile(r"\s+")
_LEAD_INS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^(?:before i (?:ask|post)(?: (?:it|this))?(?: in chat)?\s*[:,-]?\s*)",
    (r"^(?:(?:please\s+)?(?:check|see|find out)\s+(?:if|whether)\s+(?:this|it)\s+(?:was|has been)\s+(?:already\s+)?"
     r"(?:asked|answered|discussed)(?: before)?\s*[:,-]?\s*)"),
    (r"^(?:(?:has|have|did)\s+(?:anyone|anybody|someone|somebody|people|we)\s+(?:already\s+|ever\s+)?"
     r"(?:asked|answered|discussed|mentioned|covered|talked about|asked about)\s*(?:about\s+)?(?:whether\s+|if\s+)?)"),
    r"^(?:(?:please\s+)?(?:check|see|find out)\s+(?:if|whether)\s+)",
    r"^(?:(?:is|was) there (?:a|an|any) (?:thread|discussion|answer|question)\s+(?:about|on|for)\s+)",
))
_TRAILING = re.compile(r"\s+(?:was|has been|were)\s+(?:already\s+)?(?:discussed|asked|answered|covered|mentioned)"
                       r"(?:\s+before)?\s*[.?!]*$", re.IGNORECASE)


@dataclass(frozen=True)
class Followup:
    kind: FollowKind
    number: int = 0


@dataclass(frozen=True)
class Request:
    question: str  # the user's question, scope/options/write clause removed
    channels: tuple[str, ...] = ()
    months: int = DEFAULT_MONTHS
    explicit_months: bool = False
    as_json: bool = False
    wants_write: bool = False
    followup: Followup | None = None
    from_envelope: bool = False

    @property
    def query_text(self) -> str:
        """What the searches are built from: the first 1,500 characters of a long paste."""
        return self.question[:QUERY_TEXT_CHARS]

    @property
    def truncated(self) -> bool:
        return len(self.question) > QUERY_TEXT_CHARS

    @property
    def word_count(self) -> int:
        return len(re.findall(r"[A-Za-z0-9][\w'.-]*", self.question))


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def clamp_months(value: int) -> int:
    return max(MIN_MONTHS, min(MAX_MONTHS, value))


def parse(text: str, as_json: bool = False) -> Request:
    envelope = _envelope(text)
    if envelope is not None:
        return envelope
    body = text or ""
    as_json = as_json or wants_json(body)
    body = _JSON.sub(" ", body)
    followup = _followup(body)
    wants_write = bool(_WRITE.search(body))
    body = _WRITE.sub(" ", body)  # its #channel is where to post, never a search scope
    channels = tuple(dict.fromkeys(n.lower().rstrip(".") for n in _CHANNEL.findall(body)))
    body = _SCOPE_PHRASE.sub(" ", body) if channels else body
    months, explicit, body = _window(body)
    question = _SPACES.sub(" ", body).strip(" ,;:-")
    return Request(question=question, channels=channels, months=months, explicit_months=explicit,
                   as_json=as_json, wants_write=wants_write, followup=followup)


def _window(body: str) -> tuple[int, bool, str]:
    match = _MONTHS.search(body)
    if match:
        return clamp_months(int(match.group(1))), True, _MONTHS.sub(" ", body, count=1)
    match = _YEARS.search(body)
    if match:
        return clamp_months(int(match.group(1)) * 12), True, _YEARS.sub(" ", body, count=1)
    if _LAST_YEAR.search(body):
        return 12, True, _LAST_YEAR.sub(" ", body, count=1)
    return DEFAULT_MONTHS, False, body


def _followup(body: str) -> Followup | None:
    if _MORE.match(body):
        return Followup("more")
    match = _EXPAND.match(body)
    if match:
        return Followup("expand", int(match.group(1) or match.group(2)))
    match = _WIDEN.match(body)
    if match:
        value = match.group(1) or match.group(2)
        months = int(value) if value else int(match.group(3)) * 12
        return Followup("widen", clamp_months(months))
    return None


def _envelope(text: str) -> Request | None:
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("question"), str):
        return None
    channels = data.get("channels") if isinstance(data.get("channels"), list) else []
    months = data.get("months") if isinstance(data.get("months"), int) else None
    return Request(question=data["question"].strip(), as_json=True, from_envelope=True,
                   channels=tuple(str(c).lstrip("#").lower() for c in channels if str(c).strip()),
                   months=clamp_months(months) if months else DEFAULT_MONTHS, explicit_months=months is not None)


def restate(question: str) -> str:
    """The user's own question, lead-ins removed, as a copyable line. No word is added except the '?'."""
    text = _SPACES.sub(" ", question).strip()
    for pattern in _LEAD_INS:
        text = pattern.sub("", text, count=1).strip()
    text = _TRAILING.sub("", text).strip(" ,;:-")
    if not text:
        return question.strip()
    text = text[0].upper() + text[1:]
    return text if text.endswith("?") else text.rstrip(".!") + "?"


def is_arithmetic(text: str) -> bool:
    """A bare arithmetic question ("what's 2+2?"): out of scope, decided without a model call."""
    return bool(re.fullmatch(r"\s*(?:what(?:'s|\s+is)\s+|calculate\s+|compute\s+)?[\d\s.+\-*/x×^()=]*\d[\d\s.+\-*/x×^()=]*"
                             r"[+\-*/x×^][\d\s.+\-*/x×^()=]*\d\s*\??\s*", (text or "").casefold()))
