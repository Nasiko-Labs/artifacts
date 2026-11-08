"""Pure: chat messages and channels out of raw tool output. JSON bodies are walked for message-shaped
objects (a `ts` and a `text`) and channel-shaped ones (an `id` and a `name`). Nothing here trusts the
model: these are what the tools returned. (Adapted from the MCP kit pattern; owned here.)"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from index import load, walk

_SKIP_SUBTYPES = frozenset({"channel_join", "channel_leave", "group_join", "group_leave", "channel_topic",
                            "channel_purpose", "channel_name", "pinned_item", "bot_add", "bot_remove",
                            "bot_message"})


@dataclass(frozen=True)
class ChatMessage:
    ts: str
    text: str
    author: str
    user_id: str = ""
    thread_ts: str = ""
    permalink: str = ""

    @property
    def time(self) -> float | None:
        try:
            return float(self.ts)
        except ValueError:
            return None

    @property
    def is_top_level(self) -> bool:
        return not self.thread_ts or self.thread_ts == self.ts


def messages_in(text: str) -> tuple[ChatMessage, ...]:
    """Every human message in a tool body, first occurrence per ts, oldest first."""
    found: dict[str, ChatMessage] = {}
    for raw in walk(load(text)):
        message = to_message(raw) if isinstance(raw, Mapping) else None
        if message is not None:
            found.setdefault(message.ts, message)
    return tuple(sorted(found.values(), key=lambda m: m.time or 0.0))


def to_message(raw: Mapping[str, Any]) -> ChatMessage | None:
    if not isinstance(raw.get("ts"), (str, int, float)) or not isinstance(raw.get("text"), str):
        return None
    ts = str(raw["ts"])
    try:
        float(ts)
    except ValueError:
        return None
    if raw.get("subtype") in _SKIP_SUBTYPES or raw.get("bot_id") or not raw["text"].strip():
        return None
    permalink = raw.get("permalink") if isinstance(raw.get("permalink"), str) else ""
    return ChatMessage(ts=ts, text=raw["text"], author=_author(raw), user_id=str(raw.get("user") or ""),
                       thread_ts=str(raw.get("thread_ts") or ""),
                       permalink=permalink if permalink.startswith("https://") else "")


def channels_in(text: str) -> tuple[tuple[str, str], ...]:
    """(id, name) of every channel-shaped object, first occurrence per id."""
    found: dict[str, str] = {}
    for raw in walk(load(text)):
        if not isinstance(raw, Mapping) or "ts" in raw:
            continue
        cid, name = raw.get("id"), raw.get("name")
        if isinstance(cid, str) and isinstance(name, str) and cid and name:
            found.setdefault(cid, name)
    return tuple(found.items())


def _author(raw: Mapping[str, Any]) -> str:
    profile = raw.get("user_profile") if isinstance(raw.get("user_profile"), Mapping) else {}
    for value in (profile.get("first_name"), profile.get("display_name"), profile.get("real_name"),
                  raw.get("user_name"), raw.get("username"), profile.get("name")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
