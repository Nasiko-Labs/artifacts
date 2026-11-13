"""The one LLM call, as a LangChain LCEL runnable (messages | ChatOpenAI | StrOutputParser): the inbound
traceparent on every request, a per-call timeout, JSON mode, parsing, and one repair call when the answer is
not valid JSON for its contract."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableLambda

import prompts
from nasiko_trace import bound_traceparent

log = logging.getLogger("escalation.llm")

CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 300.0
MAX_TOKENS = 1_500
EXTRACT_MAX_TOKENS = 2_500


class LlmError(RuntimeError):
    """The model could not be reached, or never returned JSON matching the contract."""


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    llm: BaseChatModel  # a ChatOpenAI on OPENAI_BASE_URL / OPENAI_API_KEY; `model` is bound per call
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)

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
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining()))
    try:
        with bound_traceparent(deps.traceparent):
            text = await json_chain(deps, max_tokens, timeout).ainvoke(list(messages))
    except Exception as e:  # network, status, timeout: one class for the caller
        raise LlmError(f"model call failed: {type(e).__name__}") from e
    return str(text or "")


def _load(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def json_chain(deps: TurnDeps, max_tokens: int, timeout: float) -> Runnable:
    """messages -> chat model in JSON mode (this request's traceparent as a header) -> the reply text.

    `response_format` and `max_tokens` ride in `extra_body` on purpose: as top-level kwargs ChatOpenAI would
    send `max_completion_tokens` and route JSON mode through `completions.parse`, which the OpenAI GenAI
    instrumentor does not wrap (no token spans). This way the wire body matches the plain-SDK original and the
    call goes through the instrumented `completions.create`."""
    model = deps.llm.bind(model=deps.model, timeout=timeout, extra_headers={"traceparent": deps.traceparent},
                          extra_body={"response_format": {"type": "json_object"}, "max_tokens": max_tokens})
    return RunnableLambda(_as_messages) | model | StrOutputParser()


def _as_messages(messages: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(role, content) pairs: LangChain builds the messages as-is, never templating the braces in prompts."""
    return [(str(m["role"]), str(m["content"])) for m in messages]
