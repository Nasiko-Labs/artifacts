"""The rating step: 20 tasks per call (two calls at most), validated in code, one repair per turn, then
defaults (impact 3, effort 3) for what still fails. The model only rates; it never sees a score or rank."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import checks
import estimates
import prompts
from llm import LlmError, TurnDeps, call, load

log = logging.getLogger("prioritizer.rate")

BATCH_SIZE = 20
OK, OFF_TOPIC, FAILED = "ok", "off_topic", "failed"


@dataclass(frozen=True)
class RateResult:
    status: str
    ratings: dict[str, dict] = field(default_factory=dict)
    defaulted: int = 0          # tasks that got default ratings although their batch answered
    failed_batches: int = 0     # batches whose call failed outright


def tasks_block(tasks: Sequence[dict]) -> str:
    return "\n".join(f"{t['id']}: {' '.join(prompts.strip_markers(t['text']).split())}" for t in tasks)


def _batches(tasks: Sequence[dict]) -> list[list[dict]]:
    return [list(tasks[i:i + BATCH_SIZE]) for i in range(0, len(tasks), BATCH_SIZE)]


async def _rate_batch(deps: TurnDeps, batch: list[dict], goals: Sequence[str], instruction: str,
                      policy: Sequence[str]) -> tuple[str, dict[str, dict]]:
    ids = [t["id"] for t in batch]
    needs_effort = {t["id"] for t in batch if estimates.parse(t["text"]) is None}
    block = tasks_block(batch)
    messages = prompts.rate_messages(block, goals, instruction, sorted(needs_effort, key=ids.index), policy)
    raw = await call(deps, messages)
    data = load(raw)
    if isinstance(data, dict) and data.get("status") == OFF_TOPIC:
        return OFF_TOPIC, {}
    found, complete = checks.valid_batch(data, ids, needs_effort)
    if complete or not deps.has_time() or not deps.budget.take_repair():
        return OK, found
    log.info("ratings failed their contract; one repair call")
    try:
        repaired, _ = checks.valid_batch(load(await call(deps, prompts.repair_messages(prompts.RATE_CONTRACT, raw,
                                                                                         block))), ids, needs_effort)
    except LlmError as e:
        log.warning("repair failed: %s", e)
        repaired = {}
    return OK, {**found, **repaired}


async def rate(deps: TurnDeps, tasks: Sequence[dict], goals: Sequence[str] = (), instruction: str = "",
               policy: Sequence[str] = ()) -> RateResult:
    batches = _batches(tasks)
    results = await asyncio.gather(*(_rate_batch(deps, b, goals, instruction, policy) for b in batches),
                                   return_exceptions=True)
    ratings: dict[str, dict] = {}
    defaulted = failed = 0
    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, LlmError):
                raise result
            log.warning("rating batch failed: %s", result)
            failed += 1
            ratings.update({t["id"]: dict(checks.DEFAULT_RATING) for t in batch})
            continue
        status, found = result
        if status == OFF_TOPIC:
            return RateResult(OFF_TOPIC)
        for task in batch:
            rating = found.get(task["id"])
            if rating is None:
                defaulted += 1
                ratings[task["id"]] = dict(checks.DEFAULT_RATING)
            else:
                ratings[task["id"]] = _grounded(rating, task["text"], goals)
    if failed == len(batches):
        return RateResult(FAILED, failed_batches=failed)
    return RateResult(OK, ratings, defaulted, failed)


def _grounded(rating: dict, text: str, goals: Sequence[str]) -> dict:
    return {**rating, "impact_why": checks.clean_why(rating["impact_why"], rating["impact"], text, goals),
            "delegate_to": checks.safe_delegate(rating["delegate_to"], text), "defaulted": False}
