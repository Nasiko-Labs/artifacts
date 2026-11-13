"""The one LLM call, as a LangChain LCEL runnable (messages | ChatOpenAI | StrOutputParser): the inbound
traceparent on every request, a per-call timeout under the turn deadline, JSON mode, parsing, and a per-turn
call budget with at most two repair calls per turn."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableLambda

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("ticket-triager.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 360.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 13
MAX_REPAIRS_PER_TURN = 2
MAX_TOKENS = 3_000
TEMPERATURE = 0  # same tickets, same category and signals: no sampling spread


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


@dataclass
class Budget:
    """Mutable per turn: how many calls were made and how many repairs are left."""

    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0
    repairs_left: int = MAX_REPAIRS_PER_TURN

    def take_call(self) -> None:
        if self.calls >= self.max_calls:
            raise LlmError("call budget for this turn is spent")
        self.calls += 1

    def take_repair(self) -> bool:
        if self.repairs_left <= 0 or self.calls >= self.max_calls:
            return False
        self.repairs_left -= 1
        return True


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one. `now` is the
    wall clock for ticket ages (fixed in tests); `clock` is the monotonic clock for the deadline."""

    llm: BaseChatModel  # a ChatOpenAI on OPENAI_BASE_URL / OPENAI_API_KEY; `model` is bound per call
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)
    now: Callable[[], datetime] = _utc_now

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def has_time(self) -> bool:
        return self.remaining() >= MIN_CALL_SECS


async def complete_json(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], contract: str,
                        validate: Callable[[Any], dict | None], max_tokens: int = MAX_TOKENS) -> dict:
    """The request, then one repair call if the answer fails `validate` and a repair is left this turn."""
    raw = await call(deps, messages, max_tokens)
    parsed = validate(load(raw))
    if parsed is not None:
        return parsed
    if not deps.has_time() or not deps.budget.take_repair():
        raise LlmError("the model's answer did not match the contract and no repair is left")
    log.info("model answer failed its contract; one repair call")
    repaired = validate(load(await call(deps, prompts.repair_json_messages(contract, raw), max_tokens)))
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
            text = await json_chain(deps, max_tokens, timeout).ainvoke(list(messages))
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(text or "")


def json_chain(deps: TurnDeps, max_tokens: int, timeout: float) -> Runnable:
    """messages -> chat model in JSON mode (this request's traceparent as a header) -> the reply text.

    `response_format` and `max_tokens` ride in `extra_body` on purpose: as top-level kwargs ChatOpenAI would
    send `max_completion_tokens` and route JSON mode through `completions.parse`, which the OpenAI GenAI
    instrumentor does not wrap (no token spans). This way the wire body matches the plain-SDK original and the
    call goes through the instrumented `completions.create`."""
    model = deps.llm.bind(model=deps.model, timeout=timeout, extra_headers={"traceparent": deps.traceparent},
                          extra_body={"response_format": {"type": "json_object"}, "max_tokens": max_tokens,
                                      "temperature": TEMPERATURE})
    return RunnableLambda(_as_messages) | model | StrOutputParser()


def _as_messages(messages: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(role, content) pairs: LangChain builds the messages as-is, never templating the braces in prompts."""
    return [(str(m["role"]), str(m["content"])) for m in messages]


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text, object_pairs_hook=_merge_duplicates)
    except ValueError:
        return None


def _merge_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    """gpt-4o-mini sometimes repeats a key per item (`{"tickets": [a], "tickets": [b]}`); plain json.loads keeps
    only the last. Repeated list values are concatenated; any other repeat keeps the last value, as before."""
    out: dict = {}
    for key, value in pairs:
        if isinstance(out.get(key), list) and isinstance(value, list):
            out[key] = [*out[key], *value]
        else:
            out[key] = value
    return out
