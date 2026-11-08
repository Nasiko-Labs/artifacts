"""Pure: a previous workflow step's envelope (JSON with `items[]` = tasks) read back as the current plan.
Code re-checks it (no model call); free text next to it is the change to make."""

from __future__ import annotations

import json
import re
from typing import Any

from checks import normalize
from finalize import Context, finalize
from inputs import BANDS
from models import Plan

_FENCED = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def find(text: str) -> tuple[dict | None, str]:
    """(envelope, the rest of the message) or (None, text)."""
    raw = text or ""
    match = _FENCED.search(raw)
    span = match.span() if match else (raw.find("{"), raw.rfind("}") + 1)
    body = match.group(1) if match else raw[span[0]:span[1]]
    if span[0] < 0 or not body:
        return None, raw
    try:
        data: Any = json.loads(body)
    except ValueError:
        return None, raw
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) and "title" in i for i in items):
        return None, raw
    return data, (raw[:span[0]] + raw[span[1]:]).strip()


def to_plan(env: dict, raw_text: str) -> Plan:
    phases = [{"id": p.get("number"), "name": p.get("name")} for p in env.get("phases") or [] if isinstance(p, dict)]
    draft = normalize({"phases": phases, "tasks": env["items"], "risks": env.get("risks") or [],
                       "assumptions": env.get("assumptions") or [], "constraints": env.get("constraints") or []})
    goal = str(env.get("goal") or env.get("summary") or "the plan from the previous step")
    ctx = Context(goal=goal, source=raw_text, band=(1, BANDS["fine"][1]), renumber=not _has_ids(draft.tasks),
                  deadline=str(env.get("deadline") or ""), notes=("Read the plan from the previous step's json.",))
    return finalize(draft, ctx)


def _has_ids(tasks) -> bool:
    return all(re.fullmatch(r"T\d+", t["id"]) for t in tasks)
