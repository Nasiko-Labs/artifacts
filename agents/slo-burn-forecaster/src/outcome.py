"""Finish a forecast: burn + rank (code), corroborate the top burners (optional), headline (one checked model
call), render (code), and the envelope. Also the code-only re-render the follow-ups use."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import headline
import models
import rank
import render
from burn import Forecast
from corroborate import corroborate
from deps import TurnDeps
from models import Run

NO_SERVICES = "None of the services has enough to forecast. Paste more rows."


def now_ts(deps: TurnDeps) -> int:
    return int(deps.now.timestamp())


async def finish(deps: TurnDeps, run: Run, json_mode: bool) -> dict:
    forecasts = run.forecasts(now_ts(deps))
    if not forecasts:
        return {"reply": NO_SERVICES}
    found = await corroborate(deps, rank.top_burners(forecasts))
    run = replace(run, related=found.related, related_notes=found.notes)
    title = await headline.write(deps, forecasts)
    return {"run": run.to_dict(), **rendered(forecasts, run, title, json_mode)}


def rendered(forecasts: Sequence[Forecast], run: Run, title: str, json_mode: bool, body: str | None = None) -> dict:
    envelope = models.envelope(forecasts, run, title, render.coverage_lines(forecasts, run))
    if json_mode:
        return {"reply": render.json_block(envelope), "envelope": envelope}
    return {"reply": body if body is not None else render.report(forecasts, run, title), "envelope": envelope}
