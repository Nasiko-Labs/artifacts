"""Pure: the review load per person, computed in code.

open_requests     pull requests where the person is awaited as a reviewer (no review since the last push)
oldest_wait_days  the longest of those waits
reviews_done      reviews they submitted inside the load window (on the pull requests read this turn)
authored_open     open pull requests they authored
overloaded        open_requests >= max(OVERLOAD_FLOOR, OVERLOAD_FACTOR x median open_requests of people with >= 1)
idle_reviewer     reviews_done == 0 and open_requests >= IDLE_MIN_REQUESTS
Team requests are not expanded to members, so teams are not in the load (the coverage says so).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import median

from facts import is_bot
from queue_rules import NOBODY, TEAM_PREFIX, Assessed
from records import parse_time

OVERLOAD_FLOOR, OVERLOAD_FACTOR, IDLE_MIN_REQUESTS = 4, 2, 2


@dataclass(frozen=True)
class Load:
    person: str
    open_requests: int
    oldest_wait_days: int
    reviews_done: int
    authored_open: int
    overloaded: bool
    idle_reviewer: bool

    def to_dict(self) -> dict:
        return asdict(self)


def overload_threshold(open_counts: Sequence[int]) -> float:
    busy = [n for n in open_counts if n >= 1]
    return max(OVERLOAD_FLOOR, OVERLOAD_FACTOR * median(busy)) if busy else float(OVERLOAD_FLOOR)


def compute(assessed: Sequence[Assessed], now: datetime, window_days: int) -> tuple[list[Load], float]:
    since = now - timedelta(days=window_days)
    waits: dict[str, list[int]] = {}
    done: dict[str, int] = {}
    authored: dict[str, int] = {}
    for a in assessed:
        pr = a.facts.pr
        if pr.author and not is_bot(pr.author):
            authored[pr.author] = authored.get(pr.author, 0) + 1
        for b in a.blocked:
            if b.kind == "awaiting_reviewer" and not b.who.startswith(TEAM_PREFIX) and b.who != NOBODY:
                waits.setdefault(b.who, []).append(b.since_days)
        for r in a.facts.reviews:
            when = parse_time(r.submitted_at)
            if r.author and r.author != pr.author and not is_bot(r.author) and when and since <= when <= now:
                done[r.author] = done.get(r.author, 0) + 1
    threshold = overload_threshold([len(w) for w in waits.values()])
    people = dict.fromkeys((*waits, *done, *authored))
    rows = [_row(p, waits.get(p, []), done.get(p, 0), authored.get(p, 0), threshold) for p in people]
    rows.sort(key=lambda r: (-r.open_requests, -r.oldest_wait_days, r.person.casefold()))
    return rows, threshold


def _row(person: str, waits: list[int], done: int, authored: int, threshold: float) -> Load:
    n = len(waits)
    return Load(person, n, max(waits, default=0), done, authored, overloaded=n >= threshold and n > 0,
                idle_reviewer=done == 0 and n >= IDLE_MIN_REQUESTS)
