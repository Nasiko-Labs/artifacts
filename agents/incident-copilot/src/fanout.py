"""The fanout: five independent read loops (metrics, logs, errors, changes, history + runbook), run
concurrently behind a semaphore, each with a wall-clock cap and the whole under the turn deadline.

A loop cut off by its cap is salvaged from what its fence recorded so far (by-shape data, and records it
had already checked). A loop that fails outright becomes one coverage line; the card is built anyway.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import coverage as cov
import records as r
import shapes
import sources
from deps import TurnDeps
from investigation import Investigation
from loops import Reader

log = logging.getLogger("incident_copilot.fanout")


@dataclass
class Slot:
    """What one loop has done so far; readable after a timeout."""

    source: str
    readers: list[Reader] = field(default_factory=list)
    partial: dict = field(default_factory=dict)
    statuses: list[cov.SourceStatus] = field(default_factory=list)


@dataclass(frozen=True)
class FanoutResult:
    data: dict
    statuses: tuple[cov.SourceStatus, ...]


LoopFn = Callable[[TurnDeps, Investigation, Slot], Awaitable[None]]
LOOPS: tuple[tuple[str, LoopFn], ...] = (
    ("metrics", sources.metrics_loop),
    ("logs", sources.logs_loop),
    ("errors", sources.errors_loop),
    ("code", sources.changes_loop),
    ("history", sources.history_loop),
)


async def fanout(deps: TurnDeps, inv: Investigation) -> FanoutResult:
    gate = asyncio.Semaphore(max(deps.limits.parallel, 1))
    slots = [Slot(source) for source, _ in LOOPS]
    await asyncio.gather(*(_guarded(deps, inv, gate, fn, slot) for (_, fn), slot in zip(LOOPS, slots, strict=True)))
    data: dict = {}
    statuses: list[cov.SourceStatus] = []
    for slot in slots:
        data = {**data, **slot.partial}
        statuses.extend(slot.statuses)
    return FanoutResult(data, tuple(statuses))


def _budget(deps: TurnDeps) -> float:
    return min(deps.limits.loop_secs, deps.seconds_left() - deps.limits.summarize_secs)


async def _guarded(deps: TurnDeps, inv: Investigation, gate: asyncio.Semaphore, fn: LoopFn, slot: Slot) -> None:
    async with gate:
        budget = _budget(deps)
        if budget <= 0:
            _timed_out(slot, inv, 0.0)
            return
        try:
            await asyncio.wait_for(fn(deps, inv, slot), timeout=budget)
        except TimeoutError:
            log.warning("%s loop timed out after %.0f s; salvaging", slot.source, budget)
            _timed_out(slot, inv, budget)
        except Exception as e:  # one source failing never sinks the card
            log.exception("%s loop failed", slot.source)
            slot.statuses = [cov.SourceStatus(slot.source, "failed", type(e).__name__)]


def _timed_out(slot: Slot, inv: Investigation, secs: float) -> None:
    salvage(slot, inv)
    reader = slot.readers[0] if slot.readers else None
    offered = reader.offered_names if reader else ("?",)
    dropped = reader.dropped if reader else {}
    slot.statuses = [cov.assess(slot.source, offered, dropped, timed_out=secs or 0.0)]


def salvage(slot: Slot, inv: Investigation) -> None:
    """By-shape data from whatever the loop's tools returned before the cut-off."""
    calls = [call for reader in slot.readers for call in reader.calls]
    w = inv.windows
    if slot.source == "metrics" and "series" not in slot.partial:
        slot.partial = {**slot.partial, "series": [r.to_dict(s) for s in shapes.read_series(calls, w.obs)]}
    elif slot.source == "logs" and "log_lines" not in slot.partial:
        slot.partial = {**slot.partial, "log_lines": [r.to_dict(x) for x in shapes.read_logs(calls, w.obs)]}
    elif slot.source == "errors" and "issues" not in slot.partial:
        slot.partial = {**slot.partial, "issues": [r.to_dict(i) for i in sources.window_issues(
            shapes.read_issues(calls), w)]}
