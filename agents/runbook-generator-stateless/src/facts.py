"""extract_facts: one LLM call over the source, or a sequential map over chunks of a long transcript (no
JSON repair per chunk, so the call budget holds). Every fact is checked against the text in code."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import chunking
import llm
import prompts
from grounding import CheckedFacts, check_facts, merge

log = logging.getLogger("runbook-generator-stateless.facts")

DRAFT_RESERVE_SECS = 150.0  # a map call starts only if draft + repair still fit after it
STATUSES = ("ok", "off_topic", "no_material")


@dataclass(frozen=True)
class Extracted:
    status: str                    # ok | off_topic | no_material | failed
    checked: CheckedFacts | None = None
    parts: int = 1
    covered: int = 1
    notes: tuple[str, ...] = ()


def validate(data: object) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return None
    if data["status"] == "ok" and not isinstance(data.get("facts"), list):
        return None
    return data


async def extract(deps: llm.TurnDeps, source: str, mode: str, instruction: str,
                  policy: Sequence[str] = ()) -> Extracted:
    if len(source) <= chunking.SINGLE_PASS_MAX_CHARS:
        return await _single(deps, source, mode, instruction, policy)
    return await _map(deps, source, mode, instruction, policy)


async def _single(deps, source, mode, instruction, policy) -> Extracted:
    try:
        data = await llm.complete_json(deps, prompts.extract_messages(source, mode, instruction, policy),
                                       prompts.EXTRACT_CONTRACT, validate)
    except llm.LlmError as e:
        log.warning("extract failed: %s", e)
        return Extracted("failed")
    if data["status"] != "ok":
        return Extracted(data["status"])
    return Extracted("ok", check_facts(data, source))


async def _map(deps, source, mode, instruction, policy) -> Extracted:
    chunks = chunking.split(source)
    deps.budget.max_calls = llm.MAX_CALLS_CHUNKED
    results: list[CheckedFacts] = []
    statuses: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        if not deps.has_time(DRAFT_RESERVE_SECS):
            break
        messages = prompts.extract_messages(chunk, mode, instruction, policy, part=(index, len(chunks)))
        try:
            data = validate(llm.load(await llm.call(deps, messages)))
        except llm.LlmError as e:
            log.warning("map call %d failed: %s", index, e)
            data = None
        statuses.append(data["status"] if data else "failed")
        if data and data["status"] == "ok":
            results.append(check_facts(data, chunk))
    covered = len(statuses)
    if not results:
        status = "off_topic" if statuses and all(s == "off_topic" for s in statuses) else "failed"
        return Extracted(status, parts=len(chunks), covered=covered)
    notes = []
    failed = [i for i, s in enumerate(statuses, start=1) if s == "failed"]
    if covered < len(chunks):
        notes.append(f"Partial: I covered parts 1-{covered} of {len(chunks)} of your transcript before the time "
                     f"limit; parts {covered + 1}-{len(chunks)} were skipped.")
    if failed:
        notes.append(f"Part(s) {', '.join(map(str, failed))} of {len(chunks)} could not be read and were skipped.")
    return Extracted("ok", merge(results), parts=len(chunks), covered=covered, notes=tuple(notes))
