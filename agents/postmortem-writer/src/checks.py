"""The code checks on one chunk's extraction: the guarantee, not the prompt.

An event, factor or action survives only when its snippet is found in THAT chunk (normalised, >= 12 chars,
an ellipsis splits it) and every number, time, email and URL in its text is in the chunk too. A time the
model gives must be written on the line the snippet came from. Owners and dues follow the rules in
`owner_for` / `due_for`; anything else becomes TBD."""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding as g
from owners import due_for, owner_for
from records import ACTION_TYPES, CATEGORIES, FACTOR_CLASSES, Action, Event, Factor
from textclean import cap_snippet, clean_text, cut_words
from times import Line, parse_clock, utc_iso

STATUSES = ("ok", "off_topic", "not_incident")
WORD_CAPS = {"event": 25, "factor": 30, "action": 20}
_SPACES = re.compile(r"\s+")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")

log = logging.getLogger("postmortem.checks")


@dataclass(frozen=True)
class View:
    """One chunk: its lines, raw text, normalised text and where each line starts in it."""

    lines: tuple[Line, ...]
    text: str
    norm: str
    starts: tuple[int, ...]
    owners: tuple[Line, ...]

    def line_at(self, pos: int) -> Line:
        return self.owners[max(0, bisect.bisect_right(self.starts, pos) - 1)]


def view_of(lines: Sequence[Line]) -> View:
    starts, owners, pieces, cursor = [], [], [], 0
    for line in lines:
        norm = g.normalise(line.raw)
        if not norm:
            continue
        starts.append(cursor)
        owners.append(line)
        pieces.append(norm)
        cursor += len(norm) + 1
    text = "\n".join(line.raw for line in lines)
    return View(tuple(lines), text, " ".join(pieces), tuple(starts), tuple(owners))


@dataclass(frozen=True)
class Checked:
    events: tuple[Event, ...]
    factors: tuple[Factor, ...]
    actions: tuple[Action, ...]
    dropped: int


def parse_extract(data: Any) -> dict | None:
    """The model's JSON, shape-checked; None triggers one repair."""
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    parsed: dict = {"status": data["status"]}
    for key in ("events", "factors", "actions"):
        entries = data.get(key) or []
        if not isinstance(entries, list) or not all(isinstance(e, Mapping) for e in entries):
            return None
        parsed[key] = [dict(e) for e in entries]
    return parsed


def _drop(kind: str, reason: str) -> None:
    log.info("dropped a %s: %s", kind, reason)  # the reason only: log text stays out of the logs


def _grounded(entry: Mapping[str, Any], key: str, view: View, kind: str) -> tuple[str, str, tuple[int, int]] | None:
    text = clean_text(entry.get(key), view.text)
    snippet = _SPACES.sub(" ", str(entry.get("snippet") or "")).strip()
    if not text:
        return _drop(kind, "no text")
    span = g.locate(snippet, view.norm)
    if span is None:
        return _drop(kind, "snippet not found in the log")
    if g.ungrounded_tokens(text, view.norm):
        return _drop(kind, "a number or time not in the log")
    return text, snippet, span


def time_on_line(said: str, line: Line) -> bool:
    """The model's time is written on the quoted line: its clock (hh:mm) when it has one, else the words."""
    clocks = {(int(h), int(m)) for h, m in _CLOCK.findall(f"{line.raw} {line.time_text}")}
    found = _CLOCK.search(said)
    if found:
        return (int(found.group(1)), int(found.group(2))) in clocks
    wanted = g.normalise(said)
    return wanted in g.normalise(line.raw) or wanted == g.normalise(line.time_text)


def check_event(entry: Mapping[str, Any], view: View, event_id: str) -> Event | None:
    category = str(entry.get("category") or "").strip().casefold()
    if category not in CATEGORIES:
        return _drop("event", "unknown category")
    found = _grounded(entry, "text", view, "event")
    if found is None:
        return None
    text, snippet, span = found
    line = view.line_at(span[0])
    said = clean_text(entry.get("time"), view.text)
    if said and not time_on_line(said, line):
        return _drop("event", "its time is not on the quoted line")
    stamp, tz, time_text = line.stamp, line.tz, line.time_text
    if stamp is None and said:
        resolved = parse_clock(said, line.day)
        stamp, tz, time_text = (resolved[0], resolved[1], said) if resolved else (None, "", "")
    return Event(id=event_id, category=category, text=cut_words(text, WORD_CAPS["event"]),
                 snippet=cap_snippet(snippet), time_text=time_text, time_utc=utc_iso(stamp, tz, line.dated),
                 tz=tz if stamp else "", stamp=stamp.isoformat() if stamp else "", speaker=line.speaker,
                 line=line.n)


def check_factor(entry: Mapping[str, Any], view: View, factor_id: str, event_ids: Mapping[str, str]) -> Factor | None:
    cls = str(entry.get("class") or entry.get("cls") or "").strip().casefold()
    if cls not in FACTOR_CLASSES:
        return _drop("factor", "unknown class")
    found = _grounded(entry, "text", view, "factor")
    linked = tuple(event_ids[str(i)] for i in entry.get("event_ids") or () if str(i) in event_ids)
    if found is None:
        return None
    if not linked:
        return _drop("factor", "no checked event behind it")
    text, snippet, _ = found
    return Factor(id=factor_id, cls=cls, text=cut_words(text, WORD_CAPS["factor"]), snippet=cap_snippet(snippet),
                  inferred=entry.get("inferred") is True, event_ids=linked)


def check_action(entry: Mapping[str, Any], view: View, action_id: str, speakers: Sequence[str],
                 factor_ids: Mapping[str, str]) -> Action | None:
    found = _grounded(entry, "title", view, "action")
    if found is None:
        return None
    title, snippet, span = found
    kind = str(entry.get("type") or "").strip().casefold()
    return Action(id=action_id, title=cut_words(title, WORD_CAPS["action"]),
                  owner=owner_for(entry.get("owner"), snippet, view, span, speakers),
                  due=due_for(entry.get("due"), view, span), type=kind if kind in ACTION_TYPES else "process",
                  source="log", factor_ids=tuple(factor_ids[str(i)] for i in entry.get("factor_ids") or ()
                                                 if str(i) in factor_ids),
                  snippet=cap_snippet(snippet))
