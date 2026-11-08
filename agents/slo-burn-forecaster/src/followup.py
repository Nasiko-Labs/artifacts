"""Follow-ups on a stored forecast. Code reads the sentinels first (`what if x0.5`, `only <service>`,
`slo <service> <n>`, `show numbers`, `why <service>`); only an unclear message costs one routing model call,
whose answer is a sentinel line or OFF_TOPIC. What-if, only, SLO overrides and numbers never call a tool or a
model; `why` runs the optional corroboration for that one service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

import burn
import prompts
import render
import request
import slo
import smalltalk
from corroborate import corroborate
from deps import TurnDeps
from llm import ask_once
from models import Run
from outcome import now_ts, rendered


async def handle(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    run = Run.from_dict(state.get("run"))
    query, json_mode = state["query"], bool(state.get("json_mode"))
    services = run.services()
    target = request.named(query, services)
    overrides = slo.find(query, services)
    if overrides:
        return _override(run, deps, overrides, json_mode)
    factor = request.factor(query)
    if factor is not None:
        return _what_if(run, deps, factor, target, json_mode)
    if request.wants_numbers(query):
        return _numbers(run, deps, target, json_mode)
    if target and request.wants_why(query):
        return await _why(run, deps, target)
    if target and request.wants_only(query):
        return _only(run, deps, target, json_mode)
    if json_mode and not query.replace("json", "").strip(" ?.!").strip():
        return _numbers(run, deps, None, json_mode)
    answer = await ask_once(deps, ({"role": "system", "content": prompts.FOLLOWUP_SYSTEM},
                                   {"role": "user", "content": prompts.fence_data(query)}))
    return await _sentinel(run, deps, answer.strip(), json_mode)


def _forecasts(run: Run, deps: TurnDeps, only: str | None = None):
    found = run.forecasts(now_ts(deps))
    return tuple(f for f in found if only is None or f.name == only)


def _override(run: Run, deps: TurnDeps, overrides: Mapping[str, slo.Slo], json_mode: bool) -> dict:
    """New targets replace the stored ones; a target without a period keeps the stored period."""
    merged = dict(run.slos)
    for name, value in overrides.items():
        old = run.slos.get(name)
        merged[name] = value if value.complete or old is None else slo.Slo(value.percent, old.period_days)
    updated = replace(run, slos=merged)
    if updated.missing():
        return {"reply": prompts.SLO_PICK_FAILED}
    forecasts = _forecasts(updated, deps)
    changed = ", ".join(f"{render.safe(n)} {v.percent}%" for n, v in overrides.items())
    title = f"Recomputed with the new SLO ({changed}). " + render.template_headline(forecasts)
    return {"run": updated.to_dict(), **rendered(forecasts, updated, title, json_mode)}


def _what_if(run: Run, deps: TurnDeps, factor: Decimal, target: str | None, json_mode: bool) -> dict:
    forecasts = _forecasts(run, deps, target)
    changed = [(f, burn.what_if(f, factor)) for f in forecasts]
    body = render.what_if(changed, factor)
    title = f"What if: current burn x{factor.normalize():f}" + (f" for {render.safe(target)}" if target else "")
    return rendered([after for _, after in changed], run, title, json_mode, body)


def _numbers(run: Run, deps: TurnDeps, target: str | None, json_mode: bool) -> dict:
    forecasts = _forecasts(run, deps, target)
    return rendered(forecasts, run, render.template_headline(forecasts), json_mode, render.numbers(forecasts))


def _only(run: Run, deps: TurnDeps, target: str, json_mode: bool) -> dict:
    forecasts = _forecasts(run, deps, target)
    return rendered(forecasts, run, render.template_headline(forecasts), json_mode)


async def _why(run: Run, deps: TurnDeps, target: str) -> dict:
    forecast = _forecasts(run, deps, target)[0]
    found = await corroborate(deps, (forecast,))
    related = {**run.related, **found.related} if found.related else {k: v for k, v in run.related.items()
                                                                       if k != target}
    updated = replace(run, related=related, related_notes=found.notes)
    return {"run": updated.to_dict(),
            **rendered((forecast,), updated, render.template_headline((forecast,)), False, render.why(forecast, updated))}


async def _sentinel(run: Run, deps: TurnDeps, answer: str, json_mode: bool) -> dict:
    head, _, rest = answer.partition(":")
    head = head.strip().upper()
    services = run.services()
    if head == "WHY" or head == "ONLY":
        name = request.named(rest, services)
        if name is None:
            return {"reply": prompts.UNKNOWN_SERVICE.format(name=render.safe(rest.strip()),
                                                            names=", ".join(map(render.safe, services)))}
        return await _why(run, deps, name) if head == "WHY" else _only(run, deps, name, json_mode)
    if head == "WHATIF":
        return _sentinel_what_if(run, deps, rest, json_mode)
    if head == "SLO":
        overrides = slo.find(rest.replace(":", " ") + "%", services)
        return _override(run, deps, overrides, json_mode) if overrides else {"reply": prompts.UNCLEAR}
    if head == "NUMBERS":
        return _numbers(run, deps, None, json_mode)
    if head == "NEW_RUN":
        return {"route": "forecast"}
    if head == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"reply": prompts.UNCLEAR}


def _sentinel_what_if(run: Run, deps: TurnDeps, rest: str, json_mode: bool) -> dict:
    who, _, raw = rest.rpartition(":")
    try:
        factor = Decimal(raw.strip())
    except InvalidOperation:
        return {"reply": prompts.UNCLEAR}
    if not 0 <= factor <= request.MAX_FACTOR:
        return {"reply": prompts.UNCLEAR}
    return _what_if(run, deps, factor, request.named(who, run.services()), json_mode)
