"""The one way this agent calls a model: a CrewAI crew, kicked off once per turn.

`kickoff` turns a list of `Step`s (one role agent + its task each) into a sequential crew whose LLM is built
for this request: the router's base URL and key from env, this request's inbound `traceparent` as a default
header, JSON mode, a per-call timeout under the turn deadline. CrewAI's own retries are off
(`max_retry_limit=0`, `max_retries=0`), so a crew of N steps makes exactly N model calls. No memory, no
knowledge sources, no tools, no delegation, no planning call. A step marked `skip_if_declined` does not run
(no call) when the step before it answered `{"status": "off_topic"}` or was itself skipped. When the crew fails
part-way, the outputs of the steps that finished are on the error (`LlmError.partial`). `UnusableAnswer` (the
model answered, but not usably) is a subclass of `LlmError`; `repair.py` spends the turn's one repair call.
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

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tasks.conditional_task import ConditionalTask

from nasiko_trace import bound_traceparent
from transport import is_transport

log = logging.getLogger("prd-writer.llm")

DEFAULT_MODEL = "gpt-4o-mini"
CALL_TIMEOUT_SECS = 90.0
TURN_DEADLINE_SECS = 240.0
MIN_CALL_SECS = 1.0
MAX_CALLS_PER_TURN = 3
MAX_TOKENS = 3_000
MAX_REPAIRS_PER_TURN = 1


class LlmError(RuntimeError):
    """The model could not be reached, or the crew did not finish."""

    def __init__(self, message: str, partial: Sequence[str] = ()):
        super().__init__(message)
        self.partial = tuple(partial)


class DeadlineError(LlmError):
    """The turn deadline passed, or the turn's call budget is spent."""


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


class CallBudget:
    """Counts this turn's model calls (the one mutable piece of a turn, owned by its TurnDeps)."""

    def __init__(self, limit: int = MAX_CALLS_PER_TURN):
        self.limit, self.used, self.repairs = limit, 0, 0

    def take(self, n: int) -> None:
        if self.used - self.repairs + n > self.limit:  # the one repair call is outside the limit
            raise DeadlineError("call budget spent")
        self.used += n

    def take_repair(self) -> bool:
        if self.repairs >= MAX_REPAIRS_PER_TURN:
            return False
        self.repairs, self.used = self.repairs + 1, self.used + 1
        return True


@dataclass(frozen=True)
class TurnDeps:
    """Built per request: `traceparent` is this request's inbound header, never a cached one."""

    settings: LlmSettings
    traceparent: str | None
    clock: Callable[[], float] = time.monotonic
    deadline: float = field(default_factory=lambda: time.monotonic() + TURN_DEADLINE_SECS)
    budget: CallBudget = field(default_factory=CallBudget)

    def remaining(self) -> float:
        return self.deadline - self.clock()


@dataclass(frozen=True)
class Step:
    """One role agent and its one task. Each later step sees the earlier steps' output as context."""

    role: str
    goal: str
    backstory: str
    description: str
    expected_output: str
    skip_if_declined: bool = False


def proceeds(previous: Any) -> bool:
    """ConditionalTask's condition: the previous step ran and did not decline."""
    data = load(str(getattr(previous, "raw", "") or ""))
    return data is not None and not (isinstance(data, dict) and data.get("status") == "off_topic")


def build_llm(deps: TurnDeps, max_tokens: int, n_steps: int) -> LLM:
    timeout = max(MIN_CALL_SECS, min(CALL_TIMEOUT_SECS, deps.remaining() / max(1, n_steps)))
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
        kind = ConditionalTask if step.skip_if_declined and tasks else Task
        extra = {"condition": proceeds} if kind is ConditionalTask else {}
        task = kind(description=step.description, expected_output=step.expected_output, agent=agent,
                    context=list(tasks), **extra)
        agents.append(agent)
        tasks.append(task)
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, memory=False, planning=False,
                verbose=False)


async def kickoff(deps: TurnDeps, steps: Sequence[Step], max_tokens: int = MAX_TOKENS) -> list[str]:
    """Runs the crew once; returns each step's raw output in order."""
    if not deps.traceparent:
        raise LlmError("no inbound traceparent")  # callers check first; never call the router without it
    if deps.remaining() < MIN_CALL_SECS:
        raise DeadlineError("turn deadline passed")
    deps.budget.take(len(steps))
    return await run_crew(deps, steps, max_tokens)


async def run_crew(deps: TurnDeps, steps: Sequence[Step], max_tokens: int = MAX_TOKENS) -> list[str]:
    crew = build_crew(build_llm(deps, max_tokens, len(steps)), steps)
    try:
        with bound_traceparent(deps.traceparent):
            output = await asyncio.wait_for(crew.kickoff_async(), timeout=max(MIN_CALL_SECS, deps.remaining()))
    except TimeoutError as e:
        raise DeadlineError("turn deadline passed during the crew", _finished(crew)) from e
    except Exception as e:
        if is_transport(e):  # network, HTTP status: the model was not reached
            raise LlmError(f"crew failed: {type(e).__name__}", _finished(crew)) from e
        raise UnusableAnswer(f"crew failed on the answer: {type(e).__name__}", _finished(crew)) from e
    return [str(t.raw or "") for t in output.tasks_output]


def _finished(crew: Crew) -> list[str]:
    """The raw outputs of the leading tasks that completed before the crew stopped."""
    done: list[str] = []
    for task in crew.tasks:
        if task.output is None:
            break
        done.append(str(task.output.raw or ""))
    return done


def load(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        return None
