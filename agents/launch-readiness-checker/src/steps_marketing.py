"""Marketing step: the launch's tasks in the task manager, checked in code (due dates parsed in code)."""

from __future__ import annotations

from functools import partial

import prompts
import records
from config import MAX_TASKS
from deps import TurnDeps
from discovery import Catalog
from reader import StepOutcome, reader_for, run_step
from scope import Request


async def run(deps: TurnDeps, catalog: Catalog | None, request: Request) -> StepOutcome:
    reader = reader_for(deps, catalog, "marketing")
    system = prompts.marketing_system(request.launch or "", request.hints, deps.today)
    user = f"Find the marketing tasks for the launch {request.launch!r}."
    return await run_step(deps, reader, system, user, partial(records.tasks, cap=MAX_TASKS))
