"""Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing ever
pauses: every route ends the task `completed`. Earlier turns come only from the history block (`recall.py`).

    greeting / small talk / a bare sum             → canned reply (no call)
    another agent's envelope                       → humanize its draft fields
    a draft of ≥ 15 words ─┬─ over 20,000 chars    → humanize the first ~3k words, say what was left out
                           └─ otherwise            → humanize
      (≥ 3 words is enough after an explicit cue, "humanize: <one sentence>"; where the draft is: natural.py)
    a short follow-up, with history ─┬─ an option ("keep my em dashes", "more casual") → re-run on the draft in
                                     │     history (else on my last version), options layered over the last ones;
                                     │     "shorter" works on my last version, capped below its word count
                                     └─ show the changes / patterns / as json → rebuilt in code from the draft
                                           and my last version (no call), else re-run on the draft
    a humanize request with no draft               → the draft in history, else ask to paste it
    anything else                                  → one scope call: declined, asked for the draft, or the draft
                                                     it copied from the message (checked verbatim in code)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import checks
import envelope
import options
import prompts
import recall
import render
import smalltalk
from inputs import MAX_DRAFT_CHARS, Incoming, first_part, word_count
from natural import CUE, has_draft

COMMAND_MAX_WORDS = recall.COMMAND_MAX_WORDS
CUE_REQUEST_MAX_WORDS = 25
_CHANGES = re.compile(r"\bchanges?\b|\bwhat (?:did you|was) change(?:d)?\b|\bdiff\b")
_PATTERNS = re.compile(r"\bpatterns?\b|\bwhat did you (?:remove|find)\b")


@dataclass(frozen=True)
class Plan:
    route: str                     # reply | humanize | envelope | classify
    reply: str = ""
    draft: str = ""
    request: str = ""              # the words options are parsed from (over `base`)
    base: options.Options | None = None
    envelope: dict | None = None
    command: str = ""              # "" | changes | patterns | json: how the result is shown
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming) -> Plan:
    text = incoming.text
    if not incoming.files:
        canned = smalltalk.reply_to(text)
        if canned is not None:
            return Plan("reply", canned, notes=incoming.notes)
        if smalltalk.is_arithmetic(text):
            return Plan("reply", smalltalk.OFF_TOPIC_REPLY)
    parsed_envelope = envelope.parse(text)
    if parsed_envelope is not None:
        data, request = parsed_envelope
        return Plan("envelope", envelope=data, request=prompts.strip_markers(request), notes=incoming.notes)
    request, draft = recall.source_of_text(text)
    if incoming.files:
        request, draft = text, prompts.strip_markers("\n\n".join(body.strip() for _, body in incoming.files)).strip()
    if has_draft(request, draft):
        draft, cap_notes = within_cap(draft)
        return Plan("humanize", draft=draft, request=request, notes=(*cap_notes, *incoming.notes))
    if word_count(text) <= COMMAND_MAX_WORDS:
        follow = _follow_up(incoming)
        if follow is not None:
            return follow
    if CUE.search(text) and word_count(text) <= CUE_REQUEST_MAX_WORDS:
        return _draft_from_history(incoming)
    return Plan("classify", request=prompts.strip_markers(text), notes=incoming.notes)


def within_cap(draft: str) -> tuple[str, tuple[str, ...]]:
    if len(draft) <= MAX_DRAFT_CHARS:
        return draft, ()
    kept = first_part(draft)
    total, used = word_count(draft), word_count(kept)
    return kept, (prompts.TOO_BIG_NOTE.format(total=total, used=used, left=total - used),)


def command_of(text: str) -> str:
    plain = " ".join(text.casefold().split())
    if _PATTERNS.search(plain):
        return "patterns"
    if _CHANGES.search(plain):
        return "changes"
    return "json" if options.parse(text).as_json else ""


def _follow_up(incoming: Incoming) -> Plan | None:
    text, notes = incoming.text, incoming.notes
    changed, command = options.parse(text).changed, command_of(incoming.text)
    if not (changed or command):
        return None
    mem = recall.memory(incoming.history)
    if not mem.has_any:
        return Plan("reply", prompts.REPASTE, notes=notes)
    if mem.envelope is not None:
        return Plan("envelope", envelope=mem.envelope, request=text, base=mem.options,
                    command="" if changed else command, notes=notes)
    if changed or not (mem.draft and mem.humanized):
        return _rerun(mem, text, "" if changed else command, notes)
    shown = recall.rebuilt(mem.draft, mem.humanized)
    if command == "json":
        return Plan("reply", envelope.fenced(envelope.build(shown)), notes=notes)
    body = render.patterns_removed(shown) if command == "patterns" else render.changes(shown)
    return Plan("reply", f"{body}\n\n{prompts.DIFF_NOTE}", notes=notes)


def _rerun(mem: recall.Memory, text: str, command: str, notes: tuple[str, ...]) -> Plan:
    if options.parse(text).options.shorter and mem.humanized:
        return _shorten_last(mem, text, command, notes)
    if mem.draft:
        rebuilt = (prompts.REBUILT_SHORTER_NOTE,) if options.parse(text).options.shorter else ()
        return Plan("humanize", draft=within_cap(mem.draft)[0], request=text, base=mem.options, command=command,
                    notes=(*rebuilt, *notes))
    if command == "patterns" or not mem.humanized:   # the patterns are the original's: it is gone
        return Plan("reply", prompts.REPASTE, notes=notes)
    return Plan("humanize", draft=mem.humanized, request=text, base=mem.options, command=command,
                notes=(prompts.FROM_REPLY_NOTE, *notes))


def _shorten_last(mem: recall.Memory, text: str, command: str, notes: tuple[str, ...]) -> Plan:
    """"Make it shorter" shortens the version the user saw (already humanized), capped below its length, so the
    result is measurably shorter than that version and a failed pass falls back to it, not to the AI draft."""
    base = replace(mem.options, max_words=checks.shorter_cap(word_count(mem.humanized)))
    return Plan("humanize", draft=mem.humanized, request=text, base=base, command=command, notes=notes)


def _draft_from_history(incoming: Incoming) -> Plan:
    mem = recall.memory(incoming.history)
    if mem.envelope is not None:
        return Plan("envelope", envelope=mem.envelope, request=incoming.text,
                    notes=(prompts.FROM_HISTORY_NOTE, *incoming.notes))
    if mem.draft:
        return Plan("humanize", draft=within_cap(mem.draft)[0], request=incoming.text,
                    notes=(prompts.FROM_HISTORY_NOTE, *incoming.notes))
    return Plan("reply", prompts.NEED_DRAFT, notes=incoming.notes)
