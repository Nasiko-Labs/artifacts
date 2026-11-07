"""LLM access through the platform's router (OPENAI_BASE_URL / OPENAI_API_KEY, injected).

Every call carries the inbound `traceparent` (header, and the bound OTel context) or it is not made:
the router refuses unattributed calls. JSON-object mode, temperature 0, a per-call timeout, and a
hard per-turn call budget. Nothing here runs at import or at startup."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nasiko_trace import bound_traceparent

log = logging.getLogger("exec_brief.llm")

DEFAULT_MODEL = "gpt-4o-mini"
CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 420.0
MAX_CALLS_PER_TURN = 8
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


@dataclass
class Llm:
    """One turn's model access: client, model, traceparent, deadline and call count."""

    client: Any
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
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[dict(m) for m in messages],
                temperature=0,
                response_format={"type": "json_object"},
                extra_headers={"traceparent": self.traceparent},
                timeout=timeout,
            )
        content = response.choices[0].message.content if response.choices else None
        parsed = load_json_object(content)
        if parsed is None:
            log.info("model reply was not a JSON object: %r", (content or "")[:200])
        return parsed
