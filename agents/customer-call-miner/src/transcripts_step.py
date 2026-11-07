"""The transcripts step: the model fetches the first call's transcript (≤ 2 LLM calls); code replays the
same tool for the other call ids, substituting only the call id. Transcripts are never passed through
the model here: code keeps each raw tool result and parses it (transcript.py)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import replay
import transcript
from deps import TurnDeps
from fence import Recorded
from reader import Reader
from records import Call, Turn

log = logging.getLogger("call_miner.transcripts")

TRANSCRIPT_LOOP_CALLS = 2
REPLAY_CONCURRENCY = 4
NOT_FETCHED = "transcript not fetched"
UNREADABLE = "transcript format not readable"
NO_CUSTOMER = "no customer speech found in the transcript"


@dataclass(frozen=True)
class Fetched:
    turns: dict[str, tuple[Turn, ...]]   # call id → turns, for readable transcripts
    problems: dict[str, str]             # call id → why it was skipped


async def fetch(deps: TurnDeps, reader: Reader, calls: Sequence[Call]) -> Fetched:
    if not calls:
        return Fetched({}, {})
    first = calls[0]
    start = len(reader.records)  # earlier calls this turn (listing, details) are never a template
    messages = ({"role": "system", "content": prompts.transcript_system(first.id)},
                {"role": "user", "content": f"Fetch the transcript of call {first.id}."})
    await reader.loop(deps, messages, TRANSCRIPT_LOOP_CALLS)
    found = replay.template_for(reader.records[start:], [c.id for c in calls])
    if found is not None:
        template, value = found
        rest = [c for c in calls if not replay.covered(reader.records[start:], c.id)]
        gate = asyncio.Semaphore(REPLAY_CONCURRENCY)

        async def one(call: Call) -> None:
            async with gate:
                await reader.call(deps, template.name, replay.substitute(template.arguments, value, call.id))

        await asyncio.gather(*(one(c) for c in rest))
    else:
        log.warning("the model's transcript fetch named no call id; nothing to replay")
    return _parsed(reader.records[start:], calls)


def _parsed(records: Sequence[Recorded], calls: Sequence[Call]) -> Fetched:
    turns: dict[str, tuple[Turn, ...]] = {}
    problems: dict[str, str] = {}
    for call in calls:
        record = replay.result_for(records, call.id)
        parsed = transcript.parse(record.text, call) if record else None
        if record is None:
            problems[call.id] = NOT_FETCHED
        elif parsed is None:
            problems[call.id] = UNREADABLE
        elif not any(t.external for t in parsed):
            problems[call.id] = NO_CUSTOMER
        else:
            turns[call.id] = parsed
    return Fetched(turns, problems)
