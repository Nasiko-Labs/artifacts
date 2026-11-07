"""Pure data: the ask record, reading the model's JSON answers, what the user's message asks for
(window, source, person, fresh asks, manager), and state (de)serialisation. The checks that decide
what survives live in checks.py; ranking lives in scoring.py."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal

Status = Literal["ok", "no_read_tool", "off_topic"]
ReplyState = Literal["waiting", "replied", "unclear"]
_STATUSES = ("ok", "no_read_tool", "off_topic")
_REPLY_STATES = ("waiting", "replied", "unclear")
SOURCES = ("email", "chat")
ASK_TYPES = ("decision", "approval", "review", "input", "deliverable", "status", "info")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

DEFAULT_WINDOW_DAYS = 14
MIN_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 30
_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|three|four|a)\s*weeks?\b", re.IGNORECASE)
_MONTH = re.compile(r"\b(?:a|one|1|last|past)\s+month\b", re.IGNORECASE)
_WORD_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}
_ONLY_EMAIL = re.compile(r"\b(?:only|just)\s+(?:my\s+)?e-?mails?\b|\be-?mails?\s+only\b", re.IGNORECASE)
_ONLY_CHAT = re.compile(r"\b(?:only|just)\s+(?:my\s+)?chats?\b|\bchats?\s+only\b", re.IGNORECASE)
_FRESH = re.compile(r"\binclud(?:e|ing)\s+(?:the\s+)?(?:fresh|new|recent|today)", re.IGNORECASE)
_PERSON = re.compile(r"\b(?:waiting (?:on|for)|from|chase|nudge)\s+([A-Z][a-zA-Z'-]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)")
_NOT_PEOPLE = frozenset({"I", "Me", "Email", "Chat", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                         "Saturday", "Sunday", "Today", "Yesterday"})
_MANAGER = re.compile(
    r"\b(?:my (?:manager|boss|line manager) is|i report to)\s+([\w.+-]+@[\w-]+(?:\.[\w-]+)+|[A-Za-z][\w'-]*"
    r"(?: [A-Z][\w'-]*)?)", re.IGNORECASE)


@dataclass(frozen=True)
class Nudge:
    at: str
    link: str


@dataclass(frozen=True)
class Ask:
    id: str
    source: str
    to: str
    quote: str
    asked_at: str
    link: str
    to_name: str = ""
    conversation: str = ""
    is_group: bool = False
    ask_type: str = "info"
    gist: str = ""
    deadline_phrase: str = ""
    deadline_date: str | None = None
    blocking_quote: str | None = None
    thread_ref: str = ""
    later_nudges: tuple[Nudge, ...] = ()
    key: str = ""                      # stable within one list; outcomes are keyed by it
    reply_state: str = "waiting"
    reply_checked: bool = False
    reply_link: str | None = None

    @property
    def who(self) -> str:
        return self.to_name or self.to

    @property
    def send_to(self) -> str:
        """Where a nudge goes: the chat conversation for chat, the asked address(es) for email."""
        return self.conversation if self.source == "chat" and self.conversation else self.to


@dataclass(frozen=True)
class SweepAnswer:
    status: Status
    me: tuple[str, ...]
    asks: tuple[Ask, ...]


@dataclass(frozen=True)
class ReplyVerdict:
    state: ReplyState
    reply_link: str | None
    reply_at: str | None
    reply_from: str | None


@dataclass(frozen=True)
class Request:
    """What the user's message asks for, parsed in code."""

    window_days: int = DEFAULT_WINDOW_DAYS
    sources: tuple[str, ...] = SOURCES
    person: str | None = None
    include_fresh: bool = False


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a model reply, code fences tolerated."""
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def parse_sweep(text: str) -> SweepAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw = data.get("asks") or []
    if not isinstance(raw, list):
        return None
    me = data.get("me")
    me_list = me if isinstance(me, list) else [me]
    return SweepAnswer(status=data["status"], me=tuple(t for t in (text_of(m) for m in me_list) if t),
                       asks=tuple(a for a in (_ask(item) for item in raw) if a is not None))


def _ask(raw: Any) -> Ask | None:
    if not isinstance(raw, Mapping):
        return None
    source = text_of(raw.get("source")).lower()
    ask_type = text_of(raw.get("ask_type")).lower()
    nudges = raw.get("later_nudges") if isinstance(raw.get("later_nudges"), list) else []
    return Ask(
        id=text_of(raw.get("id")), source=source if source in SOURCES else "email", to=text_of(raw.get("to")),
        quote=text_of(raw.get("quote")), asked_at=text_of(raw.get("asked_at")), link=text_of(raw.get("link")),
        to_name=text_of(raw.get("to_name")), conversation=text_of(raw.get("conversation")),
        is_group=raw.get("is_group") is True, ask_type=ask_type if ask_type in ASK_TYPES else "info",
        gist=text_of(raw.get("gist")), deadline_phrase=text_of(raw.get("deadline_phrase")),
        deadline_date=text_of(raw.get("deadline_date")) or None,
        blocking_quote=text_of(raw.get("blocking_quote")) or None, thread_ref=text_of(raw.get("thread_ref")),
        later_nudges=tuple(Nudge(text_of(n.get("at")), text_of(n.get("link"))) for n in nudges
                           if isinstance(n, Mapping)),
    )


def parse_replies(text: str) -> dict[str, ReplyVerdict]:
    """Verdicts by ask key; anything unreadable is simply absent (the ask stays waiting, not checked)."""
    data = load_json_object(text)
    raw = data.get("results") if data else None
    if not isinstance(raw, list):
        return {}
    verdicts: dict[str, ReplyVerdict] = {}
    for item in raw:
        if not isinstance(item, Mapping) or item.get("state") not in _REPLY_STATES:
            continue
        verdict = ReplyVerdict(item["state"], text_of(item.get("reply_link")) or None,
                               text_of(item.get("reply_at")) or None, text_of(item.get("reply_from")) or None)
        verdicts = {**verdicts, text_of(item.get("id")): verdict}
    return verdicts


def parse_request(query: str) -> Request:
    text = query or ""
    email, chat = bool(_ONLY_EMAIL.search(text)), bool(_ONLY_CHAT.search(text))
    sources = ("email",) if email and not chat else ("chat",) if chat and not email else SOURCES
    person = next((m.group(1) for m in _PERSON.finditer(text) if m.group(1) not in _NOT_PEOPLE), None)
    return Request(window_days=window_days(text), sources=sources, person=person,
                   include_fresh=bool(_FRESH.search(text)))


def window_days(query: str) -> int:
    """The search window the user asked for: 14 days by default, clamped to 3-30."""
    days = _DAYS.search(query or "")
    if days:
        return _clamp(int(days.group(1)))
    weeks = _WEEKS.search(query or "")
    if weeks:
        word = weeks.group(1).lower()
        return _clamp(7 * (_WORD_NUMBERS.get(word) or int(word)))
    return MAX_WINDOW_DAYS if _MONTH.search(query or "") else DEFAULT_WINDOW_DAYS


def _clamp(days: int) -> int:
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, days))


def manager_from(query: str) -> str | None:
    """`my manager is priya@acme.example` → the address; `I report to Priya Shah` → the name."""
    match = _MANAGER.search(query or "")
    return match.group(1).strip() if match else None


def to_state(items: Sequence[Ask]) -> list[dict]:
    return [asdict(item) for item in items]


def from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[Ask, ...]:
    names = {f.name for f in fields(Ask)}
    return tuple(_from_dict(item, names) for item in raw or ())


def _from_dict(item: Mapping[str, Any], names: set[str]) -> Ask:
    values = {k: v for k, v in item.items() if k in names}
    nudges = tuple(Nudge(text_of(n.get("at")), text_of(n.get("link"))) for n in values.get("later_nudges") or ())
    return replace(Ask(**{**values, "later_nudges": ()}), later_nudges=nudges)
