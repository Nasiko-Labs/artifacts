"""Runs validated plan items with code's own functions, in a worker thread, under a cooperative 20 s
total cap: an analysis that would start after the cap, or that runs past it, is skipped with a note.
Results are numbered r1.. and their facts f1.. in order."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from menu import MENU, PlanItem
from profiling import Table
from result import Result

log = logging.getLogger("csv_to_insight.run")

ANALYSIS_CAP_SECS = 20.0


@dataclass(frozen=True)
class Ran:
    results: tuple[Result, ...]
    skipped: tuple[dict, ...]      # {"kind", "columns", "reason"}


async def run_plan(table: Table, items: Sequence[PlanItem], clock: Callable[[], float] = time.monotonic,
                   cap_secs: float = ANALYSIS_CAP_SECS, first_result: int = 1, first_fact: int = 1) -> Ran:
    start = clock()
    results: list[Result] = []
    skipped: list[dict] = []
    fact_no = first_fact
    for item in items:
        left = cap_secs - (clock() - start)
        if left <= 0:
            skipped.append({**item.to_dict(), "reason": "time limit"})
            continue
        try:
            result = await asyncio.wait_for(asyncio.to_thread(MENU[item.kind].fn, table, item.columns,
                                                              item.params()), timeout=left)
        except TimeoutError:
            skipped.append({**item.to_dict(), "reason": "time limit"})
            continue
        except Exception as e:  # noqa: BLE001 - a pandas edge case must not sink the turn
            log.warning("analysis %s failed: %s", item.kind, type(e).__name__)
            skipped.append({**item.to_dict(), "reason": "could not run on this data"})
            continue
        numbered = result.numbered(f"r{first_result + len(results)}", fact_no)
        fact_no += len(numbered.facts)
        results.append(numbered)
    return Ran(tuple(results), tuple(skipped))
