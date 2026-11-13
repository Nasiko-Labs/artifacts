"""Process one parsed meeting: a single call up to SINGLE_PASS_MAX_CHARS, map-reduce above it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import chunking
import llm
import mapreduce
import prompts
from items import Result, Turn

log = logging.getLogger("meeting-notes.process")


@dataclass(frozen=True)
class Outcome:
    status: str                  # ok | off_topic | not_meeting | failed
    result: Result | None = None
    chunked: bool = False


async def run(deps: llm.TurnDeps, turns: Sequence[Turn], speakers: Sequence[str], instruction: str,
              context: Sequence[str] = ()) -> Outcome:
    index = checks.Index(turns, speakers)
    chunks = chunking.split(turns)
    if len(chunks) > 1:
        return await _map_reduce(deps, chunks, index, instruction, context)
    try:
        parsed = await llm.complete_json(deps, prompts.notes_messages(chunking.as_text(turns), instruction, context),
                                         prompts.NOTES_CONTRACT, checks.parse_notes)
    except llm.LlmError:
        log.exception("single-pass notes failed")
        return Outcome("failed")
    if parsed["status"] != "ok":
        return Outcome(parsed["status"])
    topics, decisions, actions, dropped = checks.check_all(parsed, index)
    kept = sum(len(t.bullets) for t in topics) + len(decisions) + len(actions)
    t, d, a, capped = checks.finalize(topics, decisions, actions)
    return Outcome("ok", Result(t, d, a, kept_before_caps=kept, dropped=dropped, capped=capped))


async def _map_reduce(deps: llm.TurnDeps, chunks, index: checks.Index, instruction: str,
                      context: Sequence[str]) -> Outcome:
    mapped = await mapreduce.map_chunks(deps, chunks, index, instruction, context)
    if not mapped.parts:
        return Outcome(mapped.off_topic or "failed", chunked=True)
    result = await mapreduce.reduce(deps, mapped, index, instruction, context)
    return Outcome("ok", result, chunked=True)
