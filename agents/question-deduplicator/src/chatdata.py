"""Pure: messages and channels out of raw tool output. JSON bodies are walked for message-shaped
objects (a `ts` and a `text`) and channel-shaped ones (an `id` and a `name`). Nothing here trusts the
model: these are what the tools returned."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from models import Message

_THREAD_IN_LINK = re.compile(r"[?&]thread_ts=([0-9]+\.[0-9]+)")
_SKIP_SUBTYPES = frozenset({"channel_join", "channel_leave", "group_join", "group_leave", "channel_topic",
                            "channel_purpose", "channel_name", "pinned_item", "bot_add", "bot_remove"})


def load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def walk(node: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def messages_in(text: str, channel_id: str = "", channel_name: str = "") -> tuple[Message, ...]:
    """Every human message in a tool body, first occurrence per (channel, ts)."""
    found: dict[tuple[str, str], Message] = {}
    for raw in walk(load(text)):
        message = to_message(raw, channel_id, channel_name)
        if message is not None:
            found.setdefault((message.channel_id, message.ts), message)
    return tuple(found.values())


def to_message(raw: Mapping[str, Any], channel_id: str = "", channel_name: str = "") -> Message | None:
    if not isinstance(raw.get("ts"), (str, int, float)) or not isinstance(raw.get("text"), str):
        return None
    ts = str(raw["ts"])
    try:
        float(ts)
    except ValueError:
        return None
    if raw.get("subtype") in _SKIP_SUBTYPES or not raw["text"].strip():
        return None
    cid, cname = _channel(raw.get("channel"), channel_id, channel_name)
    cid = _first(raw.get("channel_id"), cid)
    cname = _first(raw.get("channel_name"), cname)
    if not cid:
        return None
    permalink = raw.get("permalink") if isinstance(raw.get("permalink"), str) else ""
    permalink = permalink if permalink.startswith("https://") else ""
    thread_ts = _first(str(raw.get("thread_ts") or ""), _link_thread(permalink))
    reply_count = raw.get("reply_count")
    return Message(channel_id=cid, channel_name=cname, ts=ts, user_id=str(raw.get("user") or ""),
                   author=_author(raw), text=raw["text"], thread_ts=thread_ts if thread_ts != ts else "",
                   reply_count=reply_count if isinstance(reply_count, int) else None, permalink=permalink,
                   reactions=_reactions(raw.get("reactions")))


def channels_in(text: str) -> tuple[tuple[str, str], ...]:
    """(id, name) of every channel-shaped object the user is a member of (membership unknown: kept),
    most members first when the tool says how many."""
    found: dict[str, tuple[str, str, int]] = {}
    for raw in walk(load(text)):
        cid, name = raw.get("id"), raw.get("name")
        if not isinstance(cid, str) or not isinstance(name, str) or "ts" in raw:
            continue
        if raw.get("is_member") is False or raw.get("is_archived") is True or raw.get("is_im") is True:
            continue
        members = raw.get("num_members")
        found.setdefault(cid, (cid, name, members if isinstance(members, int) else 0))
    ordered = sorted(found.values(), key=lambda c: -c[2])
    return tuple((cid, name) for cid, name, _ in ordered)


def _channel(value: Any, channel_id: str, channel_name: str) -> tuple[str, str]:
    if isinstance(value, Mapping):
        return _first(value.get("id"), channel_id), _first(value.get("name"), channel_name)
    if isinstance(value, str) and value:
        return value, channel_name
    return channel_id, channel_name


def _author(raw: Mapping[str, Any]) -> str:
    profile = raw.get("user_profile") if isinstance(raw.get("user_profile"), Mapping) else {}
    return _first(raw.get("user_name"), raw.get("username"), profile.get("real_name"), profile.get("display_name"),
                  profile.get("name"), raw.get("user")) or "unknown"


def _reactions(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(r["name"]) for r in value if isinstance(r, Mapping) and isinstance(r.get("name"), str))


def _link_thread(permalink: str) -> str:
    match = _THREAD_IN_LINK.search(permalink)
    return match.group(1) if match else ""


def _first(*values: Any) -> str:
    return next((v.strip() for v in values if isinstance(v, str) and v.strip()), "")
