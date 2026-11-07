"""Records of one catch-up and the output envelope. The envelope (a plain dict) is the single source
for rendering, for follow-ups and for the A2A data part."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Me:
    """Who "me" is: from the chat profile, or from what the user typed (`typed`)."""

    user_id: str = ""
    handle: str = ""
    display_name: str = ""
    typed: bool = False

    @property
    def known(self) -> bool:
        return bool(self.user_id or self.handle or self.display_name)


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    kind: str = "channel"  # "channel" | "dm"
    muted: bool = False
    archived: bool = False

    @property
    def label(self) -> str:
        if self.kind == "dm":
            return f"DM with {self.name or self.id}"
        return f"#{self.name or self.id}"


@dataclass(frozen=True)
class Message:
    channel_id: str
    ts: str
    user_id: str
    author: str
    text: str
    thread_ts: str = ""
    reply_count: int = 0
    is_bot: bool = False
    is_join: bool = False
    permalink: str = ""
    time_label: str = ""  # pasted messages: the time as the user wrote it

    @property
    def time(self) -> float | None:
        try:
            return float(self.ts)
        except ValueError:
            return None

    @property
    def is_human(self) -> bool:
        return not (self.is_bot or self.is_join)


@dataclass(frozen=True)
class ChannelCatchup:
    channel: str
    channel_id: str
    message_count: int
    thread_count: int = 0
    broadcasts: int = 0
    mentions: tuple[dict, ...] = ()
    decisions: tuple[dict, ...] = ()
    highlights: tuple[dict, ...] = ()
    summarized: bool = True
    note: str = ""
    dropped: int = 0
    i_posted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "channel_id": self.channel_id, "message_count": self.message_count,
                "thread_count": self.thread_count, "broadcasts": self.broadcasts,
                "mentions": list(self.mentions), "decisions": list(self.decisions),
                "highlights": list(self.highlights), "summarized": self.summarized, "note": self.note}


@dataclass(frozen=True)
class Outcome:
    """One turn's result: the reply text, the envelope (for the data part and follow-ups) and the
    `since` marker to store, which is set only after a fully rendered catch-up."""

    reply: str
    envelope: dict | None = None
    since_marker: str | None = None


@dataclass(frozen=True)
class RunFacts:
    """What code knows about the run, for confidence and coverage."""

    since: str
    until: str
    since_label: str = ""
    coverage: tuple[str, ...] = ()
    channels_unavailable: int = 0
    dropped_items: int = 0
    cap_hit: bool = False
    pasted: bool = False
    quiet: int = 0  # conversations read that had no messages in the window
    not_covered: int = 0  # channels left out by the cap
    filters: tuple[str, ...] = field(default=())


def envelope(channels: tuple[ChannelCatchup, ...], facts: RunFacts) -> dict[str, Any]:
    """The catalog envelope; `confidence` and `needs_human` are computed here, never by the model."""
    read = [c for c in channels if c.summarized]
    totals = {
        "channels_read": len(read) + facts.quiet,
        "mentions": sum(len(c.mentions) for c in read),
        "decisions": sum(len(c.decisions) for c in read),
        "messages": sum(c.message_count for c in read),
        "broadcasts": sum(c.broadcasts for c in read),
    }
    by_channel = [c.to_dict() for c in channels]
    return {
        "summary": summary_line(totals, facts.since_label or facts.since, facts.not_covered),
        "items": by_channel,
        "confidence": confidence(channels, facts),
        "needs_human": totals["mentions"] > 0,
        "by_channel": by_channel,
        "since": facts.since,
        "until": facts.until,
        "totals": totals,
        "coverage": list(facts.coverage),
    }


def confidence(channels: tuple[ChannelCatchup, ...], facts: RunFacts) -> str:
    if facts.pasted:
        return "low"
    unread = facts.channels_unavailable or any(not c.summarized for c in channels)
    dropped = facts.dropped_items or any(c.dropped for c in channels)
    return "medium" if unread or dropped or facts.cap_hit or facts.not_covered else "high"


def summary_line(totals: dict[str, int], since: str, not_covered: int = 0) -> str:
    more = f" ({not_covered} more not covered)" if not_covered else ""
    return (f"Since {since}: {totals['mentions']} mention{'s' * (totals['mentions'] != 1)}, "
            f"{totals['decisions']} decision{'s' * (totals['decisions'] != 1)} across "
            f"{totals['channels_read']} channel{'s' * (totals['channels_read'] != 1)}{more}")
