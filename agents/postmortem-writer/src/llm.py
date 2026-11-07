"""The one LLM call wrapper: the inbound traceparent on every request, a per-call timeout, JSON mode, a
per-turn call budget, and one repair call when the answer is not valid JSON for its contract."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("postmortem.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 420.0
MAP_MAX_TOKENS = 1_800
WRITE_MAX_TOKENS = 1_500
SINGLE_PASS_CALLS = 4     # extract, write, one repair each
WRITE_CALLS = 2           # the write call and one repair (JSON or blameless lint)


class LlmError(RuntimeError):
    """The model could not be reached, the turn's call budget is spent, or the answer never matched."""


class Budget:
    """Counts this turn's model calls; the one deliberately mutable object of a turn."""

    def __init__(self, limit: int = SINGLE_PASS_CALLS) -> None:
        self.limit, self.calls = limit, 0

    def plan(self, chunks: int, map_repairs: int) -> None:
        self.limit = chunks + map_repairs + WRITE_CALLS

    def take(self) -> None:
        if self.calls >= self.limit:
            raise LlmError("call budget spent")
        self.calls += 1

    def left(self) -> int:
        return self.limit - self.calls


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = WRITE_MAX_TOKENS,
                        repair: Callable[[], bool] = lambda: True) -> dict:
    """The request, then (when `repair()` allows) one repair if the answer fails `validate`."""
    raw = await call(deps, messages, max_tokens)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    if not repair():
        raise LlmError("the model's answer did not match the contract")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(load(await call(deps, prompts.repair_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
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


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
