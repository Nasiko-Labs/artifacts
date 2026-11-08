"""Pure: which roadmap issues a change affects, and the order changes are shown in.

score = |tokens(keywords + what) ∩ tokens(issue title)| / |tokens(keywords)|, capped at 1; an issue is kept
at score >= 0.34 with at least 2 shared words, at most 2 per change. Order: pricing and deprecation first,
then changes that hit an open roadmap item, then newest first."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from config import Config
from records import Change, RoadmapMatch
from words import tokens

CLOSED_STATES = frozenset({"done", "completed", "complete", "closed", "shipped", "released", "merged", "resolved"})
FIRST_CATEGORIES = ("pricing", "deprecation")


def is_closed(state: str) -> bool:
    return str(state or "").casefold().strip() in CLOSED_STATES


def matches(change: Change, issues: Iterable[Mapping[str, str]], cfg: Config) -> tuple[RoadmapMatch, ...]:
    keys = tokens(" ".join(change.keywords))
    if not keys:
        return ()
    pool = keys | tokens(change.what)
    found = []
    for issue in issues:
        shared = pool & tokens(issue["title"])
        score = round(min(1.0, len(shared) / len(keys)), 2)
        if score >= cfg.map_min_score and len(shared) >= cfg.map_min_shared:
            found.append(RoadmapMatch(id=issue["id"], title=issue["title"], url=issue.get("url") or "",
                                      state=issue.get("state") or "unknown", score=score))
    found.sort(key=lambda m: (-m.score, m.id))
    return tuple(found[:cfg.map_max_per_change])


def mapped(changes: Sequence[Change], issues: Sequence[Mapping[str, str]], cfg: Config) -> list[Change]:
    return [replace(c, matches=matches(c, issues, cfg)) for c in changes]


def _group(change: Change) -> int:
    if change.category in FIRST_CATEGORIES:
        return 0
    return 1 if change.affects is not None and not is_closed(change.affects.state) else 2


def ordered(changes: Sequence[Change]) -> list[Change]:
    by_date = sorted(changes, key=lambda c: c.date or "", reverse=True)
    return sorted(by_date, key=_group)
