"""One read step: a bounded tool loop in which the model only makes the calls, then code keeps what the
tools returned (`fence.Call` records, read by shape in records.py). The model's own words are never
used as data, so a step that ends early or hits its iteration limit loses nothing it already read: the
result is marked `partial` and the guide says so. A step never ends the turn."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import coverage
import prompts
from deps import TurnDeps
from fence import Call
from reader import STEP_SOURCE, Reader

log = logging.getLogger("onboarding.steps")

_PAUSED = ("approval_pending", "authorization_required")
_USER_TURN = "Make the calls now."


@dataclass(frozen=True)
class StepResult:
    """`calls` is this step's successful tool output; `note` says why the step read nothing."""

    calls: tuple[Call, ...] = ()
    note: str = ""
    partial: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.calls)


async def run(deps: TurnDeps, reader: Reader, step: str, system: str, extra_source: str | None = None) -> StepResult:
    source = STEP_SOURCE[step]
    before = reader.status(source)
    if before.state != "ok" and extra_source is None:
        return StepResult(note=coverage.reason(before))
    tools = reader.tools(step, extra_source)
    if not tools:
        return StepResult(note="not connected")
    messages = ({"role": "system", "content": system}, {"role": "user", "content": _USER_TURN})
    mark = reader.fenced.mark()
    result = await reader.loop(deps, step, messages, tools)
    calls = tuple(c for c in reader.fenced.since(mark) if not c.is_error)
    if result.status in _PAUSED:
        return StepResult(calls, prompts.READ_NEEDS_APPROVAL, partial=True)
    if not calls:
        after = reader.status(source)
        note = coverage.reason(after) if after.state != "ok" else prompts.NOT_READABLE
        return StepResult(note=note)
    if result.status == "max_iterations":
        log.info("step %s hit its iteration limit; keeping what it read", step)
    return StepResult(calls, partial=result.status == "max_iterations")
