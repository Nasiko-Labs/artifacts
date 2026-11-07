"""Pure: messages pulled out of whatever JSON an email tool returned, grouped into threads, in code.

Tools differ in shape, so this walks the JSON for objects that look like a message (a subject and a sender)
and reads the usual field spellings case-insensitively. Nothing is invented: a field the tool did not return
stays empty, and every text kept here is the tool's own text."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from models import Mail
from signals import address_of

_ID = ("messageid", "message_id", "id")
_THREAD = ("threadid", "thread_id", "conversationid", "conversation_id")
_FROM = ("from", "sender")
_TO = ("to", "recipients", "torecipients")
_CC = ("cc", "ccrecipients")
_DATE = ("messagetimestamp", "date", "receivedat", "received_at", "receiveddatetime", "sentat", "timestamp",
         "internaldate")
_BODY = ("messagetext", "body", "text", "plaintext", "plain_text", "textbody", "content")
_PREVIEW = ("bodypreview", "snippet")
_LABELS = ("labelids", "label_ids", "labels")
_LINK = ("link", "url", "permalink", "weblink", "web_link", "html_url")
_TOKEN = ("nextpagetoken", "next_page_token", "nextcursor", "next_cursor")
_PROFILE = ("emailaddress", "email_address", "email")
_MS_EPOCH = 10**12
_MAX_DEPTH = 12


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    sender: str
    subject: str
    to: str
    cc: str
    date: datetime | None
    body: str
    labels: tuple[str, ...]
    list_unsubscribe: bool
    precedence_bulk: bool
    link: str | None
    preview_only: bool


def load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def messages_in(text: str) -> list[Message]:
    data = load(text)
    return [] if data is None else [m for m in (_message(d) for d in _candidates(data, 0)) if m is not None]


def next_token(text: str) -> str | None:
    for node in _dicts(load(text), 0):
        low = _lower(node)
        for key in _TOKEN:
            value = low.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def profile_addresses(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for node in _dicts(load(text), 0):
        low = _lower(node)
        for key in _PROFILE:
            addr = address_of(str(low.get(key) or "")) if isinstance(low.get(key), str) else ""
            if addr:
                found.append(addr)
    return tuple(dict.fromkeys(found))


def threads_of(messages: Sequence[Message], me: Sequence[str]) -> list[Mail]:
    """Group by thread, newest message last; threads ordered newest first."""
    groups: dict[str, list[Message]] = {}
    for msg in messages:
        groups.setdefault(msg.thread_id, [])
        if all(existing.id != msg.id for existing in groups[msg.thread_id]):
            groups[msg.thread_id].append(msg)
    mails = [_mail(tid, sorted(msgs, key=_sort_key), me) for tid, msgs in groups.items()]
    return sorted(mails, key=lambda m: m.date or "", reverse=True)


def _sort_key(msg: Message) -> datetime:
    return msg.date or datetime.min.replace(tzinfo=UTC)


def _mail(thread_id: str, msgs: list[Message], me: Sequence[str]) -> Mail:
    newest = msgs[-1]
    labels = tuple(dict.fromkeys(label for m in msgs for label in m.labels))
    mine = {a.casefold() for a in me}
    return Mail(
        id=thread_id, sender=newest.sender, subject=newest.subject or next((m.subject for m in msgs if m.subject), ""),
        date=iso(newest.date), to=newest.to, cc=newest.cc, last_text=newest.body,
        text="\n".join(f"{m.subject}\n{m.body}" for m in msgs), labels=labels,
        list_unsubscribe=any(m.list_unsubscribe for m in msgs), precedence_bulk=any(m.precedence_bulk for m in msgs),
        link=newest.link or next((m.link for m in msgs if m.link), None), msg_count=len(msgs),
        last_from_me="SENT" in {label.upper() for label in newest.labels} or address_of(newest.sender) in mine,
        truncated=newest.preview_only,
    )


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candidates(node: Any, depth: int) -> Iterator[Mapping]:
    """Message-like objects; a matched object is not searched further (its preview is part of it)."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, Mapping):
        if _looks_like_message(node):
            yield node
            return
        for value in node.values():
            yield from _candidates(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _candidates(value, depth + 1)
    elif isinstance(node, str) and node.lstrip().startswith(("{", "[")):
        inner = load(node)  # some tools double-encode their payload
        if inner is not None:
            yield from _candidates(inner, depth + 1)


def _dicts(node: Any, depth: int) -> Iterator[Mapping]:
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from _dicts(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _dicts(value, depth + 1)


def _lower(node: Mapping) -> dict[str, Any]:
    return {str(k).casefold(): v for k, v in node.items()}


def _headers(low: Mapping[str, Any]) -> dict[str, str]:
    raw = low.get("headers")
    payload = low.get("payload")
    if raw is None and isinstance(payload, Mapping):
        raw = _lower(payload).get("headers")
    if isinstance(raw, Mapping):
        return {str(k).casefold(): _flat(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(h.get("name", "")).casefold(): _flat(h.get("value")) for h in raw if isinstance(h, Mapping)}
    return {}


def _looks_like_message(node: Mapping) -> bool:
    low = _lower(node)
    headers = _headers(low)
    has_subject = isinstance(low.get("subject"), str) or "subject" in headers or _preview(low).get("subject")
    has_sender = any(low.get(k) for k in _FROM) or "from" in headers
    return bool(has_subject and has_sender)


def _preview(low: Mapping[str, Any]) -> dict[str, Any]:
    preview = low.get("preview")
    return _lower(preview) if isinstance(preview, Mapping) else {}


def _first(low: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = low.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _flat(value: Any) -> str:
    """A sender or recipient list as text: 'Name <addr>' entries joined with commas."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        low = _lower(value)
        inner = low.get("emailaddress")
        if isinstance(inner, Mapping):
            return _flat(inner)
        name = str(low.get("name") or "").strip()
        addr = str(low.get("email") or low.get("address") or "").strip()
        return f"{name} <{addr}>" if name and addr else name or addr
    if isinstance(value, list):
        return ", ".join(p for p in (_flat(v) for v in value) if p)
    return str(value)


def _body(low: Mapping[str, Any]) -> tuple[str, bool]:
    for key in _BODY:
        value = low.get(key)
        if isinstance(value, Mapping):
            value = _first(_lower(value), ("content", "text", "data"))
        if isinstance(value, str) and value.strip():
            return value, False
    preview = _first(low, _PREVIEW) or _preview(low).get("body")
    return (preview, True) if isinstance(preview, str) else ("", True)


def to_datetime(value: Any) -> datetime | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        try:
            return datetime.fromtimestamp(number / 1000 if number > _MS_EPOCH else number, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _message(node: Mapping) -> Message | None:
    low = _lower(node)
    headers = _headers(low)
    msg_id = str(_first(low, _ID) or "")
    thread_id = str(_first(low, _THREAD) or msg_id)
    if not thread_id:
        return None
    body, preview_only = _body(low)
    labels = _first(low, _LABELS)
    link = _first(low, _LINK)
    return Message(
        id=msg_id or thread_id, thread_id=thread_id,
        sender=_flat(_first(low, _FROM)) or headers.get("from", ""),
        subject=str(low.get("subject") or headers.get("subject") or _preview(low).get("subject") or ""),
        to=_flat(_first(low, _TO)) or headers.get("to", ""), cc=_flat(_first(low, _CC)) or headers.get("cc", ""),
        date=to_datetime(_first(low, _DATE)) or to_datetime(headers.get("date")),
        body=body, labels=tuple(str(x) for x in labels) if isinstance(labels, list) else (),
        list_unsubscribe=bool(headers.get("list-unsubscribe")),
        precedence_bulk=headers.get("precedence", "").strip().casefold() in ("bulk", "list", "junk"),
        link=link if isinstance(link, str) and link.startswith(("http://", "https://")) else None,
        preview_only=preview_only,
    )
