"""Pure: which chat channels are read for reported statuses.

Only channels the user can see, only ones that look like status channels (a status word in the name, or
a team's name), at most 12. Direct and group messages are excluded in code: a channel the tool reported
as a direct or group conversation, an id with a direct/group prefix, or a channel whose type is unknown.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from records import Channel

MAX_CHANNELS = 12
KEEP_TYPES = frozenset({"public", "private"})
_DM_ID_PREFIXES = ("D", "G")
_STATUS_NAME = re.compile(r"status|update|standup|weekly|eng|team|launch|release|program|project", re.IGNORECASE)
_NAMED = re.compile(r"#([a-z0-9][a-z0-9_.-]{0,79})", re.IGNORECASE)


def is_direct(channel_id: str, channel_type: object) -> bool:
    """True for anything that is (or might be) a direct or group conversation."""
    if str(channel_type or "").casefold() not in KEEP_TYPES:
        return True
    return (channel_id or "").startswith(_DM_ID_PREFIXES)


def bare(name: str) -> str:
    return (name or "").strip().lstrip("#").casefold()


def named_in(text: str) -> frozenset[str]:
    """Channels the user named in the message (`#eng-status`)."""
    return frozenset(m.casefold() for m in _NAMED.findall(text or ""))


def status_like(name: str, teams: Iterable[str]) -> bool:
    plain = bare(name)
    if _STATUS_NAME.search(plain):
        return True
    words = set(re.split(r"[^a-z0-9]+", plain))
    return any(t and (bare(t) in words or bare(t).replace(" ", "-") in plain) for t in teams)


def keep(raw: Sequence[Mapping[str, Any]], is_seen, teams: Sequence[str], named: frozenset[str]) -> tuple[Channel, ...]:
    """The channels to read: seen in the tool output, not direct, status-like or named, capped (named first)."""
    picked: dict[str, Channel] = {}
    for entry in raw:
        name, cid = entry.get("name"), str(entry.get("id") or "").strip()
        if not isinstance(name, str) or not is_seen(name) or (cid and not is_seen(cid)):
            continue
        if is_direct(cid, entry.get("type")):
            continue
        if bare(name) in named or status_like(name, teams):
            picked.setdefault(bare(name), Channel(id=cid, name=bare(name)))
    ordered = sorted(picked.values(), key=lambda c: (c.name not in named,))
    return tuple(ordered[:MAX_CHANNELS])
