"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                                   -> canned intro (no call)
    a diff in the message (or in an envelope)               -> run
    a review stored ─┬─ follow-up command                   -> followup
                     ├─ new spec / standards, no diff       -> run again over the stored diff
                     └─ otherwise                           -> scope check (decline, or follow-up help)
    answer to the pause without a diff                      -> "still no diff" (no second pause)
    asks for a review, no diff                              -> pause: paste the diff (no call)
    anything else                                           -> scope check (decline, or pause)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import prompts
import smalltalk
from diff_parse import Parsed, parse
from inputs import MAX_DIFF_CHARS, Asks, Incoming, from_envelope, read_asks

_ASKS_FOR_REVIEW = re.compile(r"\breview\b|\bdiff\b|\bpatch\b|\bpull request\b|\bPR\b|\bcode review\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                         # reply | pause | run | followup | scope
    reply: str = ""
    source: str = ""                  # the diff text (and request) to review
    parsed: Parsed | None = None
    asks: Asks | None = None
    truncated: bool = False
    command: followup.Command | None = None


def asks_to_dict(asks: Asks) -> dict:
    return {"spec": asks.spec, "standards": asks.standards, "axis": asks.axis, "nits": asks.nits,
            "strict": asks.strict}


def asks_from(data: object) -> Asks | None:
    if not isinstance(data, dict):
        return None
    return Asks(spec=str(data.get("spec") or ""), standards=str(data.get("standards") or ""),
                axis=str(data.get("axis") or ""), nits=bool(data.get("nits")), strict=bool(data.get("strict")))


def _source(text: str) -> tuple[str, bool]:
    source = from_envelope(text) or text
    if len(source) <= MAX_DIFF_CHARS:
        return source, False
    cut = source[:MAX_DIFF_CHARS]
    return cut[:cut.rfind("\n")] if "\n" in cut else cut, True


def plan_turn(incoming: Incoming, stored: dict, resumed: bool = False) -> Route:
    if not resumed:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            return Route("reply", canned)
    source, truncated = _source(incoming.text)
    parsed = parse(source)
    if parsed.files:
        return Route("run", source=source, parsed=parsed, asks=read_asks(parsed.rest), truncated=truncated)
    if stored.get("envelope"):
        return _with_result(incoming, stored)
    if resumed:
        return Route("reply", prompts.NO_DIFF_AGAIN)
    if _ASKS_FOR_REVIEW.search(incoming.text):
        return Route("pause")
    return Route("scope")


def _with_result(incoming: Incoming, stored: dict) -> Route:
    command = followup.parse(incoming.text)
    if command is not None:
        return Route("followup", command=command)
    asks = read_asks(incoming.text)
    if not (asks.spec or asks.standards):
        return Route("scope")
    source = stored.get("source") or ""
    parsed = parse(source)
    old = asks_from(stored.get("asks")) or read_asks(parsed.rest)
    merged = Asks(spec=asks.spec or old.spec, standards=asks.standards or old.standards, axis=asks.axis or old.axis,
                  nits=asks.nits or old.nits, strict=asks.strict or old.strict, spec_cut=asks.spec_cut,
                  standards_cut=asks.standards_cut)
    return Route("run", source=source, parsed=parsed, asks=merged, truncated=bool(stored.get("truncated")))
