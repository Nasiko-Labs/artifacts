"""Clustering: one tool-less call groups the distinct theme labels (at most 120) into themes. The answer is
validated in code: only known labels, each label in one theme, a 2-6 word name; a label left out stays its
own theme. When the call fails or cannot be read, every label is its own theme (the fallback)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import prompts
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import parse_clusters
from themes import clean_label, key


@dataclass(frozen=True)
class Clustering:
    theme_of: Mapping[str, str]      # label key → theme name
    fallback: bool = False


def distinct(labels: Iterable[str], most: int) -> tuple[str, ...]:
    """Distinct labels (case-insensitive), most frequent first, at most `most`."""
    counts: dict[str, int] = {}
    shown: dict[str, str] = {}
    for label in labels:
        counts[key(label)] = counts.get(key(label), 0) + 1
        shown.setdefault(key(label), label)
    order = sorted(counts, key=lambda k: (-counts[k], k))
    return tuple(shown[k] for k in order[:most])


def validate(answer: tuple[tuple[str, tuple[str, ...]], ...] | None, labels: tuple[str, ...]) -> Clustering:
    known = {key(lab): lab for lab in labels}
    if answer is None:
        return Clustering({k: lab for k, lab in known.items()}, fallback=True)
    theme_of: dict[str, str] = {}
    for name, members in answer:
        own = [key(m) for m in members if key(m) in known and key(m) not in theme_of]
        if not own:
            continue
        title = clean_label(name) or known[own[0]]
        theme_of.update({k: title for k in own})
    for k, lab in known.items():
        theme_of.setdefault(k, lab)
    return Clustering(theme_of)


async def cluster(deps: TurnDeps, labels: tuple[str, ...]) -> Clustering:
    if len(labels) < 2:
        return Clustering({key(lab): lab for lab in labels})
    listing = prompts.fence("\n".join(labels))
    messages = ({"role": "system", "content": prompts.CLUSTER_SYSTEM}, {"role": "user", "content": listing})
    try:
        return validate(parse_clusters(await ask_once(deps, messages)), labels)
    except TurnLimitReached:
        return validate(None, labels)
