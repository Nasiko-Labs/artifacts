"""The one way this agent calls a model: a CrewAI crew, kicked off once per turn.

`kickoff` turns a list of `Step`s (one role agent + its task each) into a sequential crew whose LLM is built
for this request: the router's base URL and key from env, this request's inbound `traceparent` as a default
header, JSON mode, a per-call timeout under the turn deadline. CrewAI's own retries are off
(`max_retry_limit=0`, `max_retries=0`), so a crew of N steps makes exactly N model calls. No memory, no
knowledge sources, no tools, no delegation, no planning call.

`repair.kickoff_checked` adds the one repair a turn may spend (`Budget.take_repair`). Two failure classes:
`LlmError` (the model could not be reached: transport, HTTP status, timeout, deadline) and its subclass
`UnusableAnswer` (the model answered, but the answer could not be used) - catch the subclass first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from crewai_env import apply_crewai_env_defaults

apply_crewai_env_defaults()

import httpx
import openai
from crewai import LLM, Agent, Crew, Process, Task

from nasiko_trace import bound_traceparent

log = logging.getLogger("design-doc-drafter.llm")

DEFAULT_MODEL = "gpt-4o-mini"
CALL_TIMEOUT_SECS = 150.0
TURN_DEADLINE_SECS = 420.0
MIN_CALL_SECS = 5.0
MAX_CALLS_PER_TURN = 3
MAX_TOKENS = 4_000
MAX_REPAIRS_PER_TURN = 1
TRANSPORT_ERRORS = (openai.APIError, httpx.HTTPError, TimeoutError, ConnectionError)


class LlmError(RuntimeError):
    """The model could not be reached, or the crew did not finish."""


class UnusableAnswer(LlmError):
    """The model answered, but the answer could not be used (not JSON, wrong shape, empty), even after the repair."""


@dataclass(frozen=True)
class LlmSettings:
    model: str = DEFAULT_MODEL
    base_url: str | None = None
    api_key: str | None = None

    @staticmethod
    def from_env() -> LlmSettings:
        """The platform's LLM router injects OPENAI_BASE_URL and OPENAI_API_KEY."""
        return LlmSettings(model=os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or DEFAULT_MODEL,
                           base_url=os.environ.get("OPENAI_BASE_URL") or None,
                           api_key=os.environ.get("OPENAI_API_KEY") or None)


@dataclass
class Budget:
    """Mutable per turn: how many model calls were made."""

    max_calls: int = MAX_CALLS_PER_TURN
    calls: int = 0
    repairs: int = 0

    def take(self, n: int) -> None:
        if self.calls + n > self.max_calls:
            raise LlmError("call budget for this turn is spent")
        self.calls += n

    def take_repair(self) -> bool:
        """The one extra call a turn may spend on repairing an unusable answer (outside `max_calls`)."""
        if self.repairs >= MAX_REPAIRS_PER_TURN:
            return False
        self.repairs += 1
        self.calls += 1
        return True


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    settings: LlmSettings
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: Budget = field(default_factory=Budget)

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def has_time(self, reserve: float = 0.0) -> bool:
        return self.remaining() >= MIN_CALL_SECS + reserve


@dataclass(frozen=True)
class Step:
    """One role agent and its one task. Each later step sees the earlier steps' output as context."""

    role: str
    goal: str
    backstory: str
    description: str
    expected_output: str


def build_llm(deps: TurnDeps, max_tokens: int, n_steps: int) -> LLM:
    timeout = max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining() / max(1, n_steps)))
    kwargs: dict[str, Any] = {
        "model": deps.settings.model, "api_key": deps.settings.api_key or "unset",
        "default_headers": {"traceparent": deps.traceparent or ""}, "timeout": timeout, "max_retries": 0,
        "max_tokens": max_tokens, "response_format": {"type": "json_object"},
    }
    if deps.settings.base_url:
        kwargs.update(base_url=deps.settings.base_url, custom_openai=True)
    else:
        kwargs["provider"] = "openai"
    return LLM(**kwargs)


def build_crew(llm: LLM, steps: Sequence[Step]) -> Crew:
    agents, tasks = [], []
    for step in steps:
        agent = Agent(role=step.role, goal=step.goal, backstory=step.backstory, llm=llm, tools=[],
                      allow_delegation=False, max_iter=1, max_retry_limit=0, verbose=False)
        # No kickoff inputs are ever passed, so braces in user text are never interpolated.
        task = Task(description=step.description, expected_output=step.expected_output, agent=agent,
                    context=list(tasks))
        agents.append(agent)
        tasks.append(task)
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, memory=False, planning=False,
                verbose=False)


async def kickoff(deps: TurnDeps, steps: Sequence[Step], max_tokens: int = MAX_TOKENS) -> list[str]:
    """Runs the crew once; returns each step's raw output in order."""
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if not deps.has_time():
        raise LlmError("turn deadline reached")
    deps.budget.take(len(steps))
    return await run_crew(deps, steps, max_tokens)


async def run_crew(deps: TurnDeps, steps: Sequence[Step], max_tokens: int) -> list[str]:
    crew = build_crew(build_llm(deps, max_tokens, len(steps)), steps)
    try:
        with bound_traceparent(deps.traceparent):
            output = await asyncio.wait_for(crew.kickoff_async(), timeout=max(1.0, deps.remaining()))
    except Exception as e:
        if is_transport(e):  # network, HTTP status, timeout: the model was not reached
            raise LlmError(f"crew failed: {type(e).__name__}") from e
        raise UnusableAnswer(f"crew failed on the answer: {type(e).__name__}") from e  # e.g. an empty answer
    return [str(t.raw or "") for t in output.tasks_output]


def is_transport(error: BaseException) -> bool:
    """True when the error (or anything it was raised from) is a network, HTTP-status or timeout failure."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, TRANSPORT_ERRORS):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def off_topic(data: Any) -> bool:
    return isinstance(data, dict) and str(data.get("status") or "").casefold() == "off_topic"
