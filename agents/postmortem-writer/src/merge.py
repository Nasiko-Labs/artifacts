"""Pure: merge the checked parts into one ordered, de-duplicated, capped set with ids e1.., f1.., a1..

Events are ordered by parsed time, then line (an untimed event takes the time of the nearest timed event
before it in the log). Duplicates (same normalised text or quote, e.g. from the chunk overlap) collapse into
the first. Over TIMELINE_CAP rows, consecutive same-category rows fold together and the count is stated."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

import grounding as g
from checks import Checked
from records import Action, Event, Factor

TIMELINE_CAP = 60
FACTOR_CAP = 8
ACTION_CAP = 15


@dataclass(frozen=True)
class Merged:
    events: tuple[Event, ...]
    factors: tuple[Factor, ...]
    actions: tuple[Action, ...]
    dropped: int
    collapsed: int = 0
    capped: int = 0


def merge(parts: Iterable[Checked]) -> Merged:
    parts = tuple(parts)
    events, event_map = _unique(_ordered([e for p in parts for e in p.events]), _event_key)
    events, folded, fold_map = _collapse(events)
    event_ids = {e.id: f"e{n}" for n, e in enumerate(events, start=1)}
    resolve_event = {old: event_ids[_final(kept, fold_map, event_ids)] for old, kept in event_map.items()}
    events = tuple(replace(e, id=event_ids[e.id]) for e in events)

    factors, factor_map = _unique([f for p in parts for f in p.factors], lambda f: (g.normalise(f.text),))
    factors = tuple(replace(f, event_ids=_ids(f.event_ids, resolve_event)) for f in factors)
    factor_ids = {f.id: f"f{n}" for n, f in enumerate(factors[:FACTOR_CAP], start=1)}
    resolve_factor = {old: factor_ids[kept] for old, kept in factor_map.items() if kept in factor_ids}
    kept_factors = tuple(replace(f, id=factor_ids[f.id]) for f in factors[:FACTOR_CAP])

    actions, _ = _unique([a for p in parts for a in p.actions], lambda a: (g.normalise(a.title),))
    kept_actions = tuple(replace(a, id=f"a{n}", factor_ids=_ids(a.factor_ids, resolve_factor))
                         for n, a in enumerate(actions[:ACTION_CAP], start=1))
    capped = max(0, len(factors) - FACTOR_CAP) + max(0, len(actions) - ACTION_CAP)
    return Merged(events, kept_factors, kept_actions, sum(p.dropped for p in parts), folded, capped)


def _final(record_id: str, fold_map: Mapping[str, str], known: Mapping[str, str]) -> str:
    while record_id not in known:
        record_id = fold_map[record_id]
    return record_id


def _ids(ids: Sequence[str], mapping: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(mapping[i] for i in ids if i in mapping))


def _event_key(event: Event) -> tuple[str, ...]:
    return (g.normalise(event.text), g.normalise(event.snippet))


def _unique(records: Sequence, key) -> tuple[tuple, dict[str, str]]:
    """First of each duplicate; maps every record id to the id of the record kept for it."""
    seen: dict[str, str] = {}
    mapping: dict[str, str] = {}
    kept = []
    for record in records:
        keys = [k for k in key(record) if k]
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit is None:
            kept.append(record)
            hit = record.id
            for k in keys:
                seen[k] = hit
        mapping[record.id] = hit
    return tuple(kept), mapping


def _ordered(events: Sequence[Event]) -> list[Event]:
    by_line = sorted(events, key=lambda e: e.line)
    carried, keyed = "", []
    for event in by_line:
        carried = event.stamp or carried
        keyed.append(((carried, event.line), event))
    return [event for _, event in sorted(keyed, key=lambda pair: pair[0])]


def _collapse(events: tuple[Event, ...]) -> tuple[tuple[Event, ...], int, dict[str, str]]:
    if len(events) <= TIMELINE_CAP:
        return events, 0, {}
    kept: list[Event] = []
    fold_map: dict[str, str] = {}
    folded = 0
    excess = len(events) - TIMELINE_CAP
    for event in events:
        if folded < excess and kept and kept[-1].category == event.category:
            kept[-1] = replace(kept[-1], collapsed=kept[-1].collapsed + 1)
            fold_map[event.id] = kept[-1].id
            folded += 1
        else:
            kept.append(event)
    if len(kept) > TIMELINE_CAP:  # still too many distinct runs: keep the earliest
        for event in kept[TIMELINE_CAP:]:
            fold_map[event.id] = kept[TIMELINE_CAP - 1].id
        folded += len(kept) - TIMELINE_CAP
        kept = kept[:TIMELINE_CAP]
    return tuple(kept), folded, fold_map
