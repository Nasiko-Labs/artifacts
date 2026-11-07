"""Pure: which conversations to read and in what order to show them.

Read: the channels the user is in (muted and archived ones skipped when the tool says so), at most 12,
plus at most 5 direct-message conversations; a channel named in the message overrides that. Rank (for
which to summarize when time runs short): 5 x mentions + 2 x sqrt(messages) + 10 when the user posted
there; never shown. Show: channels with mentions or decisions first (mentions, then decisions, both
descending), then the rest by message count."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from models import Channel, ChannelCatchup

CHANNEL_CAP = 12
DM_CAP = 5


@dataclass(frozen=True)
class Selection:
    read: tuple[Channel, ...]
    not_covered: int  # channels left out by the cap
    skipped_quiet: int  # muted or archived
    unknown_names: tuple[str, ...] = ()  # named in the message but not among the user's channels


def select(channels: Sequence[Channel], named: Sequence[str] = ()) -> Selection:
    live = [c for c in channels if not (c.muted or c.archived)]
    skipped = len(channels) - len(live)
    if named:
        wanted = {n.casefold() for n in named}
        picked = tuple(c for c in channels if c.name.casefold() in wanted and not c.archived)
        missing = tuple(n for n in named if n.casefold() not in {c.name.casefold() for c in picked})
        return Selection(picked[:CHANNEL_CAP], 0, 0, missing)
    rooms = [c for c in live if c.kind == "channel"]
    dms = [c for c in live if c.kind == "dm"]
    return Selection((*rooms[:CHANNEL_CAP], *dms[:DM_CAP]), max(0, len(rooms) - CHANNEL_CAP), skipped)


def score(mentions: int, message_count: int, i_posted: bool) -> float:
    return 5 * mentions + 2 * math.sqrt(message_count) + (10 if i_posted else 0)


def rank(stats: Sequence[tuple[Channel, int, int, bool]]) -> tuple[Channel, ...]:
    """(channel, mentions, message_count, i_posted) → channels best first, ties by label; deterministic."""
    ordered = sorted(stats, key=lambda s: (-score(s[1], s[2], s[3]), s[0].label.casefold(), s[0].id))
    return tuple(s[0] for s in ordered)


def order(catchups: Sequence[ChannelCatchup]) -> tuple[ChannelCatchup, ...]:
    def key(c: ChannelCatchup):
        signal = bool(c.mentions or c.decisions)
        return (0 if signal else 1, -len(c.mentions), -len(c.decisions), -c.message_count, c.channel.casefold())
    return tuple(sorted(catchups, key=key))
