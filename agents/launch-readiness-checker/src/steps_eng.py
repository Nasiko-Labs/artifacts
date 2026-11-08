"""Engineering step: the launch's tracker issues, checked in code (ids and links verbatim)."""

from __future__ import annotations

from functools import partial

import prompts
import records
from config import MAX_ISSUES
from deps import TurnDeps
from discovery import Catalog
from reader import StepOutcome, reader_for, run_step
from scope import Request


async def run(deps: TurnDeps, catalog: Catalog | None, request: Request) -> StepOutcome:
    reader = reader_for(deps, catalog, "engineering")
    system = prompts.eng_system(request.launch or "", request.hints, deps.today)
    user = f"Find the issues for the launch {request.launch!r}."
    return await run_step(deps, reader, system, user, partial(records.issues, cap=MAX_ISSUES))
