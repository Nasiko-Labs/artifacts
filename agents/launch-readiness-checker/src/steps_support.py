"""Support step: macros / saved replies and help-center articles, checked in code."""

from __future__ import annotations

from functools import partial

import prompts
import records
from config import MAX_MACROS
from deps import TurnDeps
from discovery import Catalog
from reader import StepOutcome, reader_for, run_step
from scope import Request


async def run(deps: TurnDeps, catalog: Catalog | None, request: Request) -> StepOutcome:
    reader = reader_for(deps, catalog, "support")
    system = prompts.support_system(request.launch or "", request.hints, deps.today)
    user = f"Find support macros and help articles for the launch {request.launch!r}."
    return await run_step(deps, reader, system, user, partial(records.support, cap=MAX_MACROS))
