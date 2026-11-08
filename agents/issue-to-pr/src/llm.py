"""The one LLM call wrapper and the per-turn dependencies: the inbound traceparent on every request, a
per-call timeout under the turn deadline, JSON mode, and per-turn budgets (9 LLM calls, at most 2 of
them repairs; 40 tool calls). The coding engine, workspace and test runner ride along so tests can fake them."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("issue_to_pr.llm")

CALL_TIMEOUT_SECS = 60.0
TOOL_TIMEOUT_SECS = 30.0
TURN_DEADLINE_SECS = 420.0
WRITE_TURN_DEADLINE_SECS = 150.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 9
MAX_REPAIRS_PER_TURN = 2
MAX_TOOL_CALLS_PER_TURN = 40
MAX_TOKENS = 1_500


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


class BudgetSpent(RuntimeError):
    """A per-turn tool or model call budget is used up."""


@dataclass
class Budget:
    """Mutable per turn: calls made, repairs left, tool calls made."""

    max_calls: int = MAX_CALLS_PER_TURN
    max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN
    calls: int = 0
    repairs_left: int = MAX_REPAIRS_PER_TURN
    tool_calls: int = 0
    repaired: bool = False

    def take_call(self) -> None:
        if self.calls >= self.max_calls:
            raise LlmError("call budget for this turn is spent")
        self.calls += 1

    def take_repair(self) -> bool:
        if self.repairs_left <= 0 or self.calls >= self.max_calls:
            return False
        self.repairs_left -= 1
        self.repaired = True
        return True

    def take_tool_call(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetSpent("tool call budget for this turn is spent")
        self.tool_calls += 1


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: Any
    model: str
    gateway: Any
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)
    engine: Any = None
    workspace: Any = None
    runner: Any = None
    context_id: str = ""

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def has_time(self, reserve: float = 0.0) -> bool:
        return self.remaining() >= MIN_CALL_SECS + reserve


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict:
    """The request, then one repair call if the answer fails `validate` and a repair is left."""
    raw = await call(deps, messages, max_tokens)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    if not deps.has_time() or not deps.budget.take_repair():
        raise LlmError("the model's answer did not match the contract and no repair is left")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(load(await call(deps, prompts.repair_messages(contract, raw), max_tokens)))
    if repaired is None:
        raise LlmError("the model's answer did not match the contract after one repair")
    return repaired


async def call(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_tokens: int = MAX_TOKENS) -> str:
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if not deps.has_time():
        raise LlmError("turn deadline reached")
    deps.budget.take_call()
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


def is_off_topic(data: Any) -> bool:
    return isinstance(data, dict) and str(data.get("status") or "").casefold() == "off_topic"
