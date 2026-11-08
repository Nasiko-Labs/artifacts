"""Pure: the model's desk answers -> verified tickets, replies and macros.

Everything the model returns is checked against this turn's tool output (`seen_text`): a ticket id, url,
comment text or macro body that no tool returned is dropped. Window, cap and order are decided here.
A reply is a PUBLIC comment by an AGENT; customer comments and private notes never become replies and are
never sent to the model again. The desk's extra scrub inputs are applied here: the customer's name becomes
{{customer_name}}, the agent's own name is deleted, the ticket number becomes {{number}}."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from parse_replies import MAX_REPLY_CHARS, Reply

_SPACES = re.compile(r"\s+")
_ESCAPES = re.compile(r"\\[nrt]|\\")
_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


class Comment(BaseModel):
    id: str = ""
    author: str = ""
    author_role: str = ""
    public: bool = True
    posted_at: str = ""
    text: str = ""


class Ticket(BaseModel):
    id: str
    url: str = ""
    subject: str = ""
    solved_at: str = ""
    requester_name: str = ""
    comments: list[Comment] = []


class TicketsAnswer(BaseModel):
    status: str = "ok"
    tickets: list[Ticket] = []


class Macro(BaseModel):
    id: str = ""
    title: str = ""
    body: str = ""


class MacrosAnswer(BaseModel):
    status: str = "ok"
    macros: list[Macro] = []


@dataclass(frozen=True)
class Verified:
    tickets: tuple[Ticket, ...]
    replies: tuple[Reply, ...]
    urls: dict
    dropped: int        # tickets or comments the model returned that no tool did
    extracted: int
    capped: bool


def norm(text: str) -> str:
    """Whitespace/case/escape-normalised, so JSON-escaped tool text and copied text compare equal."""
    decoded = _UNICODE.sub(lambda m: chr(int(m.group(1), 16)), (text or "").replace('\\"', '"'))
    return _SPACES.sub(" ", _ESCAPES.sub(" ", decoded)).strip().casefold()


def _load(raw: str) -> Any:
    text = (raw or "").strip()
    fenced = _FENCED.match(text)
    text = fenced.group(1) if fenced else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def parse_tickets(raw: str) -> TicketsAnswer | None:
    try:
        return TicketsAnswer.model_validate(_load(raw))
    except ValidationError:
        return None


def parse_macros(raw: str) -> MacrosAnswer | None:
    try:
        return MacrosAnswer.model_validate(_load(raw))
    except ValidationError:
        return None


def _in_window(solved_at: str, today: date, days: int) -> bool:
    found = _DATE.search(solved_at or "")
    if not found:
        return True  # unknown date: kept, the search was already dated
    try:
        solved = date.fromisoformat(found.group(1))
    except ValueError:
        return True
    return today - timedelta(days=days) <= solved <= today


def _scrubbed(text: str, ticket: Ticket, author: str) -> str:
    out = text
    names = [ticket.requester_name.strip(), *ticket.requester_name.strip().split()[:1]]
    for name in sorted({n for n in names if len(n) >= 2}, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(name)}\b", "{{customer_name}}", out)
    for name in sorted({n for n in (author.strip(), *author.strip().split()[:1]) if len(n) >= 2}, key=len,
                       reverse=True):
        out = re.sub(rf"\b{re.escape(name)}\b", "", out)
    if ticket.id.strip():
        out = re.sub(rf"#?\b{re.escape(ticket.id.strip())}\b", "{{number}}", out)
    return out


def verify_tickets(answer: TicketsAnswer, seen_text: str, today: date, days: int, cap: int) -> Verified:
    seen = norm(seen_text)
    real = [t for t in answer.tickets if t.id.strip() and norm(t.id) in seen]
    dropped = len(answer.tickets) - len(real)
    in_window = [t for t in real if _in_window(t.solved_at, today, days)]
    ordered = sorted(enumerate(in_window), key=lambda p: (p[1].solved_at or "", -p[0]), reverse=True)
    kept = tuple(t for _, t in ordered[:cap])
    replies, urls, extracted = [], {}, len(answer.tickets)
    for ticket in kept:
        if ticket.url and norm(ticket.url) in seen:
            urls[ticket.id] = ticket.url
        for comment in ticket.comments:
            extracted += 1
            if not comment.text.strip() or norm(comment.text) not in seen:
                dropped += 1
                continue
            if comment.author_role.casefold() != "agent" or not comment.public:
                continue
            text = _scrubbed(comment.text, ticket, comment.author)[:MAX_REPLY_CHARS]
            replies.append(Reply(id=f"r{len(replies) + 1}", text=text, ticket=ticket.id,
                                 agent=comment.author.strip() or "unknown", subject=ticket.subject))
    return Verified(kept, tuple(replies), urls, dropped, extracted, len(in_window) > cap)


def verify_macros(answer: MacrosAnswer, seen_text: str, cap: int) -> tuple[tuple[str, ...], bool]:
    """(macro texts, titles_only): a body no tool returned is dropped; without bodies, titles are used."""
    seen = norm(seen_text)
    bodies = [m.body for m in answer.macros if m.body.strip() and norm(m.body) in seen][:cap]
    if bodies:
        return tuple(bodies), False
    titles = [m.title for m in answer.macros if m.title.strip() and norm(m.title) in seen][:cap]
    return tuple(titles), bool(titles)
