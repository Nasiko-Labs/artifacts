"""Render a standup from the stored records of this conversation: people, classification, gist,
coverage notes, envelope. Used by turn 1 (after reading) and by every follow-up (no reading)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import envelope
import prompts
import render
from deps import TurnDeps
from gist import gists as ask_gists
from identity import (
    Person,
    everyone,
    has_code_identity,
    has_tracker_identity,
    named_people,
)
from intake import Request
from records import Records, from_json
from render import View
from standup import PersonStandup, classify
from window import Window


@dataclass(frozen=True)
class Outcome:
    reply: str
    data: dict | None = None
    stored: dict | None = None  # kept in the checkpoint for follow-ups
    pause: dict | None = None  # the scope question: what to resume from


async def render_stored(deps: TurnDeps, stored: dict, view: View, json_mode: bool, with_gist: bool) -> Outcome:
    records = from_json(stored["records"])
    window = Window.from_dict(stored["window"])
    request = Request.from_dict(stored["request"])
    people, left_out = _people(request, records, stored.get("corrections") or {})
    standups = [classify(p, records, window) for p in people]
    if with_gist and not view.shorter:
        stored = {**stored, "gists": await _gists(deps, standups)}
    gist_map = {(name, ref): text for name, ref, text in stored.get("gists") or ()}
    notes = _notes(stored, people, records, left_out)
    scope = _scope_text(request)
    markdown = render.standup(standups, scope, window, view, gist_map)
    if not standups or all(p.silent for p in standups) and not view.reviews:
        who = ", ".join(p.label for p in people) or "anyone"
        markdown = f"{markdown}\n{prompts.NOTHING_FOUND.format(people=who, window=window.describe())}"
    quality = envelope.Quality(sources_ok=len(stored.get("covered") or ()), failed=bool(stored.get("coverage")),
                               gaps=bool(notes[len(stored.get("coverage") or ()):]))
    data = envelope.build(standups, markdown, view, quality, window.describe())
    reply = envelope.as_json_text(data) if json_mode else "\n\n".join(
        p for p in (markdown, render.coverage_block(notes), render.READ_ONLY_NOTE) if p)
    return Outcome(reply, data, stored)


def _people(request: Request, records: Records, corrections: dict) -> tuple[tuple[Person, ...], int]:
    if request.people:
        return named_people(request.people, records, corrections), 0
    return everyone(records)


async def _gists(deps: TurnDeps, standups: Sequence[PersonStandup]) -> list[list[str]]:
    lines = [(p.name, line.ref, line.title) for p in standups
             for line in (*p.shipped, *p.in_progress, *p.blocked) if line.title and line.kind in ("pr", "issue")]
    if not lines:
        return []
    ids = {f"L{n + 1}": line for n, line in enumerate(lines)}
    kept = await ask_gists(deps, {k: v[2] for k, v in ids.items()})
    return [[ids[k][0], ids[k][1], text] for k, text in kept.items()]


def _notes(stored: dict, people: Sequence[Person], records: Records, left_out: int) -> list[str]:
    """Source notes first (they decide `failed`), then identity, cap, step-limit and size notes (gaps)."""
    notes = list(stored.get("coverage") or ())
    if set(stored.get("covered") or ()) == {"code", "tracker"} and records.issues and (records.prs or records.commits):
        for person in people:
            on_code, on_tracker = has_code_identity(person, records), has_tracker_identity(person, records)
            if on_code and not on_tracker:
                notes.append(f"tracker identity for {person.label} not found (say \"{person.label} is <name> in "
                             "the tracker\" to link it).")
            elif on_tracker and not on_code:
                notes.append(f"code host identity for {person.label} not found.")
    notes += [f"record cap reached for {what}: the most recent {what} only." for what in records.cuts]
    notes += [f"{source} hit its step limit; its part may be incomplete." for source in stored.get("partial") or ()]
    if left_out:
        notes.append(f"{left_out} more people were active; name them to include them.")
    return notes


def _scope_text(request: Request) -> str:
    parts = list(request.repos)
    if request.project:
        parts.append(f"{request.project} project")
    return " + ".join(parts)


def labels(stored: dict) -> tuple[str, ...]:
    """The people the stored standup shows, as labelled there."""
    request = Request.from_dict(stored["request"])
    people, _ = _people(request, from_json(stored["records"]), stored.get("corrections") or {})
    return tuple(p.label for p in people)
