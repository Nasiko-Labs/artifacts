"""Pure: decide what a turn does, in code, before any model call."""

from __future__ import annotations

from dataclasses import dataclass, field

import brief as b
import followup
import hitl
import options as opts
import prompts
from inputs import Incoming, with_envelope_as_data, without_json_words
from text import words

NEW_BRIEF_MIN_CHARS = 200


@dataclass(frozen=True)
class Route:
    kind: str                          # new | followup | revise | scope | reply | pause
    brief: b.Brief | None = None
    command: followup.Command | None = None
    reply: str = ""
    pending: hitl.Pending | None = None
    notes: tuple[str, ...] = field(default=())


def _looks_like_new_brief(text: str, parsed: b.Brief) -> bool:
    return len(parsed.options) >= 2 or bool(parsed.criteria) or len(text) >= NEW_BRIEF_MIN_CHARS


def plan_turn(incoming: Incoming, has_memo: bool) -> Route:
    text = incoming.text.strip()
    command = followup.parse(text)
    if command.kind:
        return Route("followup", command=command) if has_memo else Route("reply", reply=prompts.NO_MEMO_YET)
    if len(text) > hitl.MAX_BRIEF_CHARS:
        return Route("pause", pending=hitl.Pending(hitl.OVERSIZE, text[:hitl.MAX_BRIEF_CHARS], chars=len(text),
                                                   wants_json=incoming.wants_json))
    source = with_envelope_as_data(without_json_words(text))
    parsed = b.parse(source)
    if has_memo and not _looks_like_new_brief(text, parsed):
        return Route("revise", command=followup.Command("revise", text))
    return prepare(source, parsed, incoming.wants_json)


def prepare(source: str, parsed: b.Brief, wants_json: bool, notes: tuple[str, ...] = ()) -> Route:
    count = len(parsed.options)
    if count == 0:
        if not words(source):
            return Route("reply", reply=prompts.NO_MEMO_YET)
        return Route("scope", reply=source, pending=hitl.Pending(hitl.NO_OPTIONS, source, wants_json=wants_json))
    if count > opts.MAX_OPTIONS:
        return Route("pause", pending=hitl.Pending(hitl.TOO_MANY, source, count=count, wants_json=wants_json))
    ready = b.with_options(parsed, opts.with_status_quo(parsed.options))
    if count == 1:
        notes = (*notes, f'You gave one option, so I added "{opts.STATUS_QUO}" as option B to compare against.')
    return Route("new", brief=ready, notes=notes)


def plan_resume(answer: hitl.Answer, pending: hitl.Pending) -> Route:
    if answer.action != "run":
        return Route("reply", reply=prompts.CANCELLED)
    notes = (answer.note,) if answer.note else ()
    parsed = b.parse(answer.text)
    if answer.keys:
        kept = tuple(o for o in parsed.options if o.key in answer.keys)
        if not kept:
            return Route("reply", reply=prompts.CANCELLED)
        parsed = b.with_options(parsed, kept[:opts.MAX_OPTIONS])
        if parsed.preference and parsed.preference not in {o.key for o in kept}:
            parsed = b.replace(parsed, preference="")
    if not parsed.options and pending.kind == hitl.NO_OPTIONS:
        return Route("reply", reply=prompts.CANCELLED)
    return prepare(answer.text, parsed, pending.wants_json, notes)
