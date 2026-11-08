"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout, JSON mode,
parsing, and one repair call when the answer is not valid JSON for its contract."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("macro_gap_finder_live.llm")
T = TypeVar("T")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 300.0
MAX_TOKENS = 1_500
CONCURRENCY = 4
DRAFT_RESERVE_SECS = 20.0  # the draft window closes this long before the turn deadline
MAX_CALLS = 14


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


class Budget:
    """Model calls made this turn; a call past `limit` is refused before it is sent. One per turn."""

    def __init__(self, limit: int = MAX_CALLS) -> None:
        self.limit = limit
        self.calls = 0

    def take(self) -> None:
        if self.calls >= self.limit:
            raise LlmError("the turn's model-call budget is spent")
        self.calls += 1


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)
    gateway: Any = None                                   # the MCP gateway client; None when not injected
    today: date = field(default_factory=date.today)

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict:
    """At most two calls: the request, then one repair if the answer fails `validate`."""
    raw = await _call(deps, messages, max_tokens)
    parsed = validate(_load(raw))
    if parsed is not None:
        return parsed
    log.info("model answer failed its contract; one repair call")
    repaired = validate(_load(await _call(deps, prompts.repair_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def _call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() <= 0:
        raise LlmError("the turn deadline has passed")
    deps.budget.take()
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            response = await deps.llm.chat.completions.create(
                model=deps.model, messages=[dict(m) for m in messages], max_tokens=max_tokens,
                response_format={"type": "json_object"}, timeout=timeout,
                extra_headers={"traceparent": deps.traceparent},
            )
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(response.choices[0].message.content or "")


def _load(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


async def bounded(jobs: Sequence[Callable[[], Awaitable[T]]], window_secs: float,
                  limit: int = CONCURRENCY) -> tuple[list[T | None], int]:
    """Runs jobs `limit` at a time; jobs still running when the window closes are cancelled.
    Returns each job's result (None when it was cut or raised) and how many were cut."""
    gate = asyncio.Semaphore(limit)

    async def one(job: Callable[[], Awaitable[T]]) -> T:
        async with gate:
            return await job()

    tasks = [asyncio.create_task(one(job)) for job in jobs]
    if not tasks:
        return [], 0
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, window_secs))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results: list[T | None] = []
    for task in tasks:
        failed = task not in done or task.cancelled() or task.exception() is not None
        if failed and task in done and not task.cancelled():
            log.warning("a bounded job failed: %s", type(task.exception()).__name__)
        results.append(None if failed else task.result())
    return results, len(pending)
