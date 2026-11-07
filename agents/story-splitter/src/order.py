"""Pure: dependency order. Stories are sorted topologically (stable: ties keep their current order), cycles
are broken by dropping the later story's edge, and the walking skeleton (the root story covering the most
capabilities with the fewest rules) goes first. Capping keeps a prefix, so no kept story loses a dependency."""

from __future__ import annotations

from dataclasses import dataclass, replace

from stories import Story


@dataclass(frozen=True)
class Ordered:
    stories: tuple[Story, ...]
    skeleton: str = ""                      # key of the walking skeleton
    broken_cycles: int = 0
    capped: tuple[str, ...] = ()            # titles dropped by the cap


def topo(stories: list[Story]) -> tuple[list[Story], int]:
    """(stories in dependency order, number of edges dropped to break cycles)."""
    position = {s.key: i for i, s in enumerate(stories)}
    pending = {s.key: s for s in stories}
    deps = {s.key: {d for d in s.depends_on if d in pending and d != s.key} for s in stories}
    done: list[Story] = []
    broken = 0
    while pending:
        ready = sorted((k for k in pending if not deps[k] & pending.keys()), key=position.__getitem__)
        if not ready:  # a cycle: the earliest remaining story loses its edges to the rest
            victim = min(pending, key=position.__getitem__)
            broken += len(deps[victim] & pending.keys())
            deps[victim] -= pending.keys()
            pending[victim] = replace(pending[victim], depends_on=tuple(
                d for d in pending[victim].depends_on if d not in pending))
            continue
        key = ready[0]
        done.append(pending.pop(key))
    return done, broken


def skeleton_key(stories: list[Story]) -> str:
    roots = [s for s in stories if not s.depends_on and not s.is_spike]
    if not roots:
        return stories[0].key if stories else ""

    def score(story: Story) -> int:
        caps = sum(1 for b in story.basis if b.startswith("c"))
        rules = sum(1 for b in story.basis if b.startswith("r"))
        return caps - rules

    best = max(score(s) for s in roots)
    return next(s.key for s in roots if score(s) == best)


def arrange(stories: list[Story], limit: int, choose_skeleton: bool = True) -> Ordered:
    """Sorted, skeleton first (or, when `choose_skeleton` is off, the order kept as is), capped at `limit`."""
    ordered, broken = topo(stories)
    first = skeleton_key(ordered) if choose_skeleton else (ordered[0].key if ordered else "")
    if first:
        ordered = [s for s in ordered if s.key == first] + [s for s in ordered if s.key != first]
    kept, cut = ordered[:limit], ordered[limit:]
    live = {s.key for s in kept}
    kept = [replace(s, depends_on=tuple(d for d in s.depends_on if d in live)) for s in kept]
    return Ordered(tuple(kept), first if first in live else "", broken, tuple(s.title for s in cut))


def chain(stories: list[Story], key: str) -> list[Story]:
    """`key` and everything it transitively depends on, in the stories' order."""
    by_key = {s.key: s for s in stories}
    wanted: set[str] = set()
    stack = [key]
    while stack:
        current = stack.pop()
        if current in wanted or current not in by_key:
            continue
        wanted.add(current)
        stack += list(by_key[current].depends_on)
    return [s for s in stories if s.key in wanted]


def move_before(stories: list[Story], key: str, anchor: str) -> list[Story]:
    """`key` placed just before `anchor`, then re-sorted so dependencies still come first."""
    moving = next(s for s in stories if s.key == key)
    rest = [s for s in stories if s.key != key]
    at = next(i for i, s in enumerate(rest) if s.key == anchor)
    return topo([*rest[:at], moving, *rest[at:]])[0]
