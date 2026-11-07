"""The shared shape of a read step: discover the step's tools, check its source is reachable, run the bounded
tool loop, and re-check reachability after (a tool may have been dropped mid-step). Returns the parsed
answer or the coverage line saying why the source was not read."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import coverage
import replies
from deps import TurnDeps
from discovery import STEP_SOURCE, Step, discover
from reader import TurnReader


@dataclass(frozen=True)
class SourceRun:
    parsed: Any = None
    read: bool = False              # the source's tools were reachable and the step ran
    notes: tuple[str, ...] = ()
    fatal: str = ""                 # a fixed reply that ends the whole turn (no trace context, gateway down)
    limited: bool = False           # the step hit its step or time limit


_FATAL = (replies.NO_TRACE_CONTEXT, replies.GATEWAY_UNREACHABLE)


async def read_source(deps: TurnDeps, turn: TurnReader, step: Step, system: str, user: str, loop_calls: int,
                      tool_calls: int, parse: Callable[[str], Any]) -> SourceRun:
    source = STEP_SOURCE[step]
    catalog, failure = await discover(deps, step)
    if failure is not None:
        if failure in _FATAL:
            return SourceRun(fatal=failure)
        return SourceRun(notes=(coverage.line(coverage.SourceStatus(source, "missing")),))
    tools = catalog.step_tools(source)
    status = coverage.assess(source, tools, catalog.source_of)
    if not status.ok:
        return SourceRun(notes=(coverage.line(status),))
    run = await turn.run_json(deps, tools, system, user, loop_calls, tool_calls, parse)
    status = coverage.assess(source, tools, catalog.source_of, turn.health.dropped)
    if not status.ok:
        return SourceRun(notes=(coverage.line(status),))
    if run.status == "paused":
        return SourceRun(notes=run.notes)
    limited = run.status in ("limit", "max_iterations")
    if run.parsed is None:
        label = coverage.LABELS[source]
        return SourceRun(read=True, notes=(*run.notes, f"The {label} answer could not be verified, so it was not used."),
                         limited=limited)
    return SourceRun(run.parsed, True, run.notes, limited=limited)
