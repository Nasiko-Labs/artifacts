"""Pure: per-event flags, decided in code.

back_to_back (starts < 5 min after the previous meeting ends), double_booked (overlaps another; the note
names both), no_description (empty description and no attachments), long (> 90 min), external (an
attendee's domain differs from the user's, else the organizer's, else the majority domain),
needs_response, tentative.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import tzinfo

import gaps as g
import timewin
from models import Event

LONG_MINUTES = 90


def flagged(events: Sequence[Event], tz: tzinfo) -> tuple[Event, ...]:
    ordered = sorted(events, key=lambda e: (e.start, e.end))
    doubles = g.overlaps(ordered)
    out = []
    for i, event in enumerate(ordered):
        flags: list[str] = []
        notes: list[str] = []
        previous_end = max((e.end for e in ordered[:i] if e.end <= event.start), default=None)
        if previous_end is not None and event.start - previous_end < g.BACK_TO_BACK:
            flags.append("back_to_back")
        for pair in doubles:
            other = pair.second if pair.first is event else pair.first if pair.second is event else None
            if other is not None:
                flags.append("double_booked")
                notes.append(f"overlaps {other.raw.title} ({timewin.clock(other.start, tz)}-"
                             f"{timewin.clock(other.end, tz)})")
        if not event.raw.description and not event.raw.has_attachments:
            flags.append("no_description")
        if event.minutes > LONG_MINUTES:
            flags.append("long")
        if is_external(event):
            flags.append("external")
        if event.status in ("needs_response", "tentative"):
            flags.append(event.status)
        out.append(event.with_flags(tuple(flags), tuple(notes)))
    return tuple(out)


def is_external(event: Event) -> bool:
    domains = [_domain(a) for a in event.raw.attendee_emails if _domain(a)]
    if not domains:
        return False
    home = _domain(event.raw.my_email) or _domain(event.raw.organizer) or Counter(domains).most_common(1)[0][0]
    return any(d != home for d in domains)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1].casefold() if "@" in (address or "") else ""
