"""Pure: which sources count as independent. Two sources are dependent when they share a non-empty author,
are both `Type: internal` with the same label prefix (first word), or one contains >= SYNDICATED_SHARE of the
other's 8-word shingles (a copy or syndication). Dependent sources form one group and count once."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import combinations

import grounding as g
from model import Source

SYNDICATED_SHARE = 0.30


def groups(sources: Sequence[Source]) -> dict[str, str]:
    """source id -> the id of its group's first source."""
    parent = {s.id: s.id for s in sources}

    def root(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    shingles = {s.id: g.shingles(s.text) for s in sources}
    for a, b in combinations(sources, 2):
        if dependent(a, b, shingles[a.id], shingles[b.id]):
            ra, rb = root(a.id), root(b.id)
            first, second = sorted((ra, rb), key=lambda i: int(i[1:]))
            parent[second] = first
    return {s.id: root(s.id) for s in sources}


def dependent(a: Source, b: Source, sa: frozenset, sb: frozenset) -> bool:
    if a.author.strip() and a.author.strip().casefold() == b.author.strip().casefold():
        return True
    if a.type == b.type == "internal" and _prefix(a.label) == _prefix(b.label):
        return True
    return any(small and len(sa & sb) / len(small) >= SYNDICATED_SHARE for small in (sa, sb))


def _prefix(label: str) -> str:
    words = label.casefold().split()
    return words[0] if words else ""


def independent_count(source_ids: Iterable[str], grouping: dict[str, str]) -> int:
    return len({grouping.get(s, s) for s in source_ids})
