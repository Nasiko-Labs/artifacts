"""Pure: decide what one turn does, before any LLM call. Nothing is stored between turns: earlier work is
whatever `recall.Prior` finds in the platform's chat history. No turn ever pauses.

    greeting / small talk                                   -> canned intro (no call)
    a diff in the message (or in an envelope)               -> run (with the request that asked for it, when
                                                               our last reply asked for the diff)
    a review recalled ─┬─ follow-up command                 -> followup
                       ├─ new spec / standards, no diff     -> run again over the recalled diff
                       └─ otherwise                         -> scope check (decline, or follow-up help)
    our last reply asked for the diff, still none           -> "still no diff" (no call)
    a follow-up, history sent but nothing recalled          -> "paste the diff again" (trimmed)
    asks for a review, no diff                              -> reply asking for the diff (no call)
    anything else                                           -> scope check (decline, or ask for the diff)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import prompts
import smalltalk
from diff_parse import Parsed, parse
from inputs import MAX_DIFF_CHARS, Asks, Incoming, from_envelope, read_asks
from recall import Prior

_ASKS_FOR_REVIEW = re.compile(r"\breview\b|\bdiff\b|\bpatch\b|\bpull request\b|\bPR\b|\bcode review\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                         # reply | run | followup | scope
    reply: str = ""
    source: str = ""                  # the diff text (and request) to review
    parsed: Parsed | None = None
    asks: Asks | None = None
    truncated: bool = False
    command: followup.Command | None = None


def _source(text: str) -> tuple[str, bool]:
    source = from_envelope(text) or text
    if len(source) <= MAX_DIFF_CHARS:
        return source, False
    cut = source[:MAX_DIFF_CHARS]
    return cut[:cut.rfind("\n")] if "\n" in cut else cut, True


def plan_turn(incoming: Incoming, prior: Prior) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    source, truncated = _source(incoming.text)
    parsed = parse(source)
    if parsed.files:
        if prior.asked_for:  # the paste after "Paste the diff": keep that request's spec / standards
            source, truncated = _source(f"{prior.asked_for}\n\n{incoming.text}")
            parsed = parse(source)
        return Route("run", source=source, parsed=parsed, asks=read_asks(parsed.rest), truncated=truncated)
    if prior.has_result:
        return _with_result(incoming, prior)
    if prior.asked_for:
        return Route("reply", prompts.NO_DIFF_AGAIN)
    if incoming.had_history and (followup.parse(incoming.text) is not None or _new_asks(incoming.text)):
        return Route("reply", prompts.NEED_REPASTE)
    if _ASKS_FOR_REVIEW.search(incoming.text):
        return Route("reply", prompts.NO_DIFF_LEAD)
    return Route("scope")


def _new_asks(text: str) -> bool:
    asks = read_asks(text)
    return bool(asks.spec or asks.standards)


def _with_result(incoming: Incoming, prior: Prior) -> Route:
    command = followup.parse(incoming.text)
    if command is not None:
        return Route("followup", command=command)
    asks = read_asks(incoming.text)
    if not (asks.spec or asks.standards):
        return Route("scope")
    if not prior.source:
        return Route("reply", prompts.NEED_REPASTE)
    source, truncated = _source(prior.source)
    parsed = parse(source)
    old = read_asks(parsed.rest)
    merged = Asks(spec=asks.spec or old.spec, standards=asks.standards or old.standards, axis=asks.axis or old.axis,
                  nits=asks.nits or old.nits, strict=asks.strict or old.strict, spec_cut=asks.spec_cut,
                  standards_cut=asks.standards_cut)
    return Route("run", source=source, parsed=parsed, asks=merged, truncated=truncated)
