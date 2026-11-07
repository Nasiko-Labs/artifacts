"""One scan of a paste: split into messages (code) → whose messages (code) → extract (1..N calls) → checks
and due dates (code) → state pass (one call) → merge into the conversation's list (code)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import checks
import llm
import messages as msgs
import state_pass
from envelope import Stats
from extract import extract
from inputs import my_name
from promises import Promise


@dataclass(frozen=True)
class Scanned:
    status: str                        # ok | off_topic | failed | no_messages
    promises: tuple[Promise, ...] = ()  # the merged list
    stats: Stats = field(default_factory=Stats)  # this paste only


async def scan(deps: llm.TurnDeps, source: str, instruction: str, as_of: date, as_of_explicit: bool,
               stored: Sequence[Promise], context: Sequence[str] = ()) -> Scanned:
    every = msgs.split(source, as_of)
    mine, guess = msgs.whose(every, my_name(instruction))
    if not mine:
        return Scanned("no_messages")
    extracted = await extract(deps, mine, instruction, context)
    if extracted.status != "ok":
        return Scanned(extracted.status)
    checked = checks.check(extracted.candidates, mine, as_of, "as_of" if as_of_explicit else "clock",
                           start_order=len(stored))
    judged = await state_pass.apply_states(deps, checked.promises, every)
    merged, _duplicates = checks.merge(stored, judged)
    stats = Stats(messages=len(mine), candidates=checked.candidates, dropped=checked.dropped, parts=extracted.parts,
                  failed_parts=extracted.failed_parts, timed_out_parts=extracted.timed_out_parts,
                  first_person=sum(msgs.first_person_count(m.text) for m in mine), speaker_guess=guess,
                  as_of_explicit=as_of_explicit)
    return Scanned("ok", merged, stats)
