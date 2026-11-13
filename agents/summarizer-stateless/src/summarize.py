"""Summarize one source: a single pass up to SINGLE_PASS_MAX_CHARS, map-reduce above it (mapreduce.py)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import chunking
import llm
import mapreduce
import prompts
from items import Item, Summary

log = logging.getLogger("summarizer.summarize")


@dataclass(frozen=True)
class Result:
    status: str                    # ok | off_topic | not_summarizable | failed
    summary: Summary | None = None
    pool: tuple[Item, ...] = ()    # map-reduce: every checked item of every finished part
    parts: int = 1


async def summarize(deps: llm.TurnDeps, source: str, instruction: str, context: Sequence[str] = ()) -> Result:
    if len(source) > chunking.SINGLE_PASS_MAX_CHARS:
        return await _map_reduce(deps, source, instruction, context)
    try:
        parsed = await llm.complete_json(deps, prompts.summarize_messages(source, instruction, context),
                                         prompts.SUMMARY_CONTRACT, checks.parse_summary)
    except llm.LlmError:
        log.exception("single-pass summary failed")
        return Result("failed")
    if parsed["status"] != "ok":
        return Result(parsed["status"])
    return Result("ok", checks.checked_summary(parsed, source))


async def _map_reduce(deps: llm.TurnDeps, source: str, instruction: str, context: Sequence[str]) -> Result:
    chunks = chunking.split(source)
    mapped = await mapreduce.map_chunks(deps, chunks, instruction, context)
    if not mapped.parts:
        status = "off_topic" if mapped.off_topic else "failed"
        return Result(status, parts=len(chunks))
    summary = await mapreduce.reduce(deps, mapped, source, instruction, context)
    return Result("ok", summary, mapped.pool, len(chunks))
