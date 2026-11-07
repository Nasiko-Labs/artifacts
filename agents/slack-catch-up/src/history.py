"""Pure: messages out of raw tool output. Each history or thread call is attributed to the conversation
whose id is among its arguments; its JSON body is walked for message-shaped objects (a `ts` and a
`text`). Nothing here trusts the model: the messages are what the tools returned."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from fence import CallRecord
from models import Channel, Message

MAX_MESSAGES = 200  # per conversation, latest kept
_JOIN_SUBTYPES = frozenset({"channel_join", "channel_leave", "group_join", "group_leave", "channel_topic",
                            "channel_purpose", "channel_name", "pinned_item", "bot_add", "bot_remove"})


def channel_of(call: CallRecord, channels: Sequence[Channel]) -> Channel | None:
    values = {str(v) for v in call.arguments.values() if isinstance(v, (str, int))}
    return next((c for c in channels if c.id in values), None)


def messages_in(text: str, channel_id: str) -> tuple[Message, ...]:
    try:
        body = json.loads(text)
    except ValueError:
        return ()
    found: dict[str, Message] = {}
    for raw in _walk(body):
        message = _message(raw, channel_id)
        if message is not None:
            found.setdefault(message.ts, message)
    return tuple(found.values())


def _walk(node: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        if _looks_like_message(node):
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _looks_like_message(node: Mapping[str, Any]) -> bool:
    return isinstance(node.get("ts"), (str, int, float)) and isinstance(node.get("text"), str)


def _message(raw: Mapping[str, Any], channel_id: str) -> Message | None:
    ts = str(raw["ts"])
    try:
        float(ts)
    except ValueError:
        return None
    profile = raw.get("user_profile") if isinstance(raw.get("user_profile"), Mapping) else {}
    user_id = str(raw.get("user") or "")
    author = _first(raw.get("user_name"), raw.get("username"), profile.get("display_name"),
                    profile.get("real_name"), profile.get("name"), user_id) or "unknown"
    permalink = raw.get("permalink")
    thread_ts = str(raw.get("thread_ts") or "")
    reply_count = raw.get("reply_count")
    return Message(channel_id=channel_id, ts=ts, user_id=user_id, author=author, text=raw["text"],
                   thread_ts=thread_ts, reply_count=reply_count if isinstance(reply_count, int) else 0,
                   is_bot=bool(raw.get("bot_id")) or raw.get("subtype") == "bot_message",
                   is_join=raw.get("subtype") in _JOIN_SUBTYPES,
                   permalink=permalink if isinstance(permalink, str) and permalink.startswith("https://") else "")


def _first(*values: Any) -> str:
    return next((v.strip() for v in values if isinstance(v, str) and v.strip()), "")


def collect(calls: Sequence[CallRecord], channels: Sequence[Channel]) -> tuple[dict[str, tuple[Message, ...]],
                                                                             dict[str, str]]:
    """(channel id → messages, channel id → error text) over these calls."""
    messages: dict[str, dict[str, Message]] = {}
    errors: dict[str, str] = {}
    for call in calls:
        channel = channel_of(call, channels)
        if channel is None:
            continue
        if call.is_error:
            errors.setdefault(channel.id, call.text)
            continue
        own = messages.setdefault(channel.id, {})
        for message in messages_in(call.text, channel.id):
            own.setdefault(message.ts, message)
        errors.pop(channel.id, None)
    return {cid: tuple(sorted(m.values(), key=_order)) for cid, m in messages.items()}, errors


def in_window(messages: Iterable[Message], start: datetime, end: datetime) -> tuple[tuple[Message, ...], int]:
    """Messages inside [start, end], oldest first, the latest MAX_MESSAGES kept; and how many were in range."""
    lo, hi = start.timestamp(), end.timestamp()
    kept = tuple(m for m in sorted(messages, key=_order) if m.time is not None and lo <= m.time <= hi)
    return kept[-MAX_MESSAGES:], len(kept)


def merge(base: Mapping[str, tuple[Message, ...]], extra: Mapping[str, tuple[Message, ...]]) -> dict[str, tuple[Message, ...]]:
    out: dict[str, tuple[Message, ...]] = {}
    for cid in (*base, *(k for k in extra if k not in base)):
        by_ts = {m.ts: m for m in base.get(cid, ())}
        for m in extra.get(cid, ()):
            by_ts.setdefault(m.ts, m)
        out[cid] = tuple(sorted(by_ts.values(), key=_order))
    return out


def _order(message: Message) -> float:
    return message.time if message.time is not None else 0.0
