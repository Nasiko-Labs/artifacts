"""Pure: decide what a turn does, in code, before any model call. Nothing here pauses: what the original
asked about (no options, too many options, an oversize brief) now gets the sensible default and says so."""

from __future__ import annotations

from dataclasses import dataclass, field

import brief as b
import followup
import options as opts
import prompts
import recall
from inputs import Incoming
from text import words


@dataclass(frozen=True)
class Route:
    kind: str                          # new | followup | scope | reply
    brief: b.Brief | None = None
    command: followup.Command | None = None
    reply: str = ""
    source: str = ""                   # scope: the text that held no options
    notes: tuple[str, ...] = field(default=())


def plan_turn(incoming: Incoming, prior: recall.Prior) -> Route:
    text = incoming.text.strip()
    command = followup.parse(text)
    if command.kind:
        if prior.source:
            return Route("followup", command=command)
        return Route("reply", reply=prompts.NOT_IN_HISTORY if incoming.history else prompts.NO_MEMO_YET)
    notes = _oversize_note(text)
    source = recall.source_of(text)
    parsed = b.parse(source)
    combined = recall.answering_options(incoming.history, text)
    if combined and not recall.is_full_brief(text, parsed):  # the options, after this agent said it found none
        return prepare(recall.source_of(combined), b.parse(recall.source_of(combined)), notes)
    if prior.source and not recall.looks_like_new_brief(text, parsed):
        return Route("followup", command=followup.Command("revise", text))
    return prepare(source, parsed, notes)


def _oversize_note(text: str) -> tuple[str, ...]:
    if len(text) <= recall.MAX_BRIEF_CHARS:
        return ()
    left_out = len(text) - recall.MAX_BRIEF_CHARS
    note = (f"That brief is {len(text):,} characters; I wrote from the first {recall.MAX_BRIEF_CHARS:,} and left "
            f"out the last {left_out:,}. Paste a shorter brief if something important was in that part.")
    return (note,)


def prepare(source: str, parsed: b.Brief, notes: tuple[str, ...] = ()) -> Route:
    count = len(parsed.options)
    if count == 0:
        if not words(source):
            return Route("reply", reply=prompts.NO_MEMO_YET)
        return Route("scope", source=source)
    if count > opts.MAX_OPTIONS:
        parsed, notes = _first_options(parsed, notes)
    ready = b.with_options(parsed, opts.with_status_quo(parsed.options))
    if count == 1:
        notes = (*notes, f'You gave one option, so I added "{opts.STATUS_QUO}" as option B to compare against.')
    return Route("new", brief=ready, notes=notes)


def _first_options(parsed: b.Brief, notes: tuple[str, ...]) -> tuple[b.Brief, tuple[str, ...]]:
    """A memo compares at most MAX_OPTIONS: keep the first ones and name the rest."""
    kept, dropped = parsed.options[:opts.MAX_OPTIONS], parsed.options[opts.MAX_OPTIONS:]
    trimmed = b.with_options(parsed, kept)
    if trimmed.preference and trimmed.preference not in {o.key for o in kept}:
        trimmed = b.replace(trimmed, preference="")
    left_out = "; ".join(f"{o.key} ({o.name})" for o in dropped)
    note = (f"You gave {len(parsed.options)} options; a memo compares at most {opts.MAX_OPTIONS}, so I compared "
            f"{kept[0].key} to {kept[-1].key} and left out {left_out}. To compare a different set, send the brief "
            "again with just those options.")
    return trimmed, (*notes, note)
