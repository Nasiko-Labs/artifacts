"""LLM access through the platform's router (OPENAI_BASE_URL / OPENAI_API_KEY, injected), as a LangChain
LCEL chain: `ChatOpenAI.bind(...) | StrOutputParser()`.

Every call carries the inbound `traceparent` (header, and the bound OTel context) or it is not made:
the router refuses unattributed calls. JSON-object mode, temperature 0, a per-call timeout, and a
hard per-turn call budget. No LangChain memory: each call sees only the messages built for it. Nothing
here runs at import or at startup."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import convert_to_messages
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from nasiko_trace import bound_traceparent

log = logging.getLogger("exec_brief.llm")

DEFAULT_MODEL = "gpt-4o-mini"
TEMPERATURE = 0
CLIENT_RETRIES = 2  # the OpenAI client's own default, as the original agent used
CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 420.0
MAX_CALLS_PER_TURN = 9  # 6 map + compose + repair, plus the follow-up scope check on a revision
_JSON_START = "{"


class MissingTraceparentError(RuntimeError):
    """No inbound traceparent: the router would refuse the call, so none is made."""


class CallBudgetError(RuntimeError):
    """This turn already made MAX_CALLS_PER_TURN calls."""


def load_json_object(text: str | None) -> dict | None:
    """The first JSON object in the reply (tolerates a code fence or prose around it), else None."""
    if not text:
        return None
    start = text.find(_JSON_START)
    end = text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def build_model(model: str, *, base_url: str | None = None, api_key: str | None = None,
                max_retries: int = CLIENT_RETRIES, http_async_client: Any = None) -> ChatOpenAI:
    """The chat model. The LLM router injects OPENAI_BASE_URL / OPENAI_API_KEY, read here unless given;
    constructing it makes no call."""
    return ChatOpenAI(model=model, api_key=api_key or os.environ.get("OPENAI_API_KEY") or "unset",
                      base_url=base_url or os.environ.get("OPENAI_BASE_URL"), temperature=TEMPERATURE,
                      max_retries=max_retries, http_async_client=http_async_client)


def json_chain(model: BaseChatModel, traceparent: str, timeout: float) -> Runnable:
    """messages -> JSON-mode completion -> raw text. The traceparent rides on the request headers.

    `response_format` goes in `extra_body` on purpose: as a top-level kwarg ChatOpenAI routes JSON mode through
    `completions.parse`, which the OpenAI GenAI instrumentor does not wrap (no token spans). This way the call
    goes through the instrumented `completions.create` and the wire body matches the plain-SDK original."""
    bound = model.bind(extra_body={"response_format": {"type": "json_object"}}, timeout=timeout,
                       extra_headers={"traceparent": traceparent})
    return bound | StrOutputParser()


@dataclass
class Llm:
    """One turn's model access: the chat model, its name, traceparent, deadline and call count."""

    client: BaseChatModel
    model: str
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline_secs: float = TURN_DEADLINE_SECS
    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0
    started: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.started = self.clock()

    def remaining(self) -> float:
        return self.deadline_secs - (self.clock() - self.started)

    async def json(self, messages: Sequence[Mapping[str, Any]]) -> dict | None:
        """One JSON-object call → the parsed object, or None when the reply is not one."""
        if not self.traceparent:
            raise MissingTraceparentError("an LLM call needs the inbound traceparent")
        if self.calls >= self.max_calls:
            raise CallBudgetError(f"turn budget of {self.max_calls} LLM calls spent")
        self.calls += 1
        timeout = max(1.0, min(CALL_TIMEOUT_SECS, self.remaining()))
        with bound_traceparent(self.traceparent):
            chain = json_chain(self.client, self.traceparent, timeout)
            content = await chain.ainvoke(convert_to_messages([dict(m) for m in messages]))
        parsed = load_json_object(content)
        if parsed is None:
            log.info("model reply was not a JSON object: %r", (content or "")[:200])
        return parsed
