"""The plan step: one model call (plus one repair) that names analyses from the closed menu. Code then
validates every item; with none valid, or with the model unreachable, code's default plan runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import llm
import prompts
from menu import (
    MAX_ANALYSES,
    PlanItem,
    chart_index,
    default_plan,
    top_up,
    validate_plan,
)
from profiling import Profile

STATUSES = ("ok", "off_topic", "missing_column")
TOP_KEYS = frozenset({"status", "analyses", "chart", "term"})
TERM_MAX_CHARS = 60


@dataclass(frozen=True)
class Planned:
    status: str                         # ok | off_topic | missing_column
    items: tuple[PlanItem, ...] = ()
    chart: int | None = None
    dropped: int = 0
    used_default: bool = False
    term: str = ""


def parse_plan(data: Any) -> dict | None:
    """Shape only (a `code` key or any other extra top-level key fails it); items are checked later."""
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES or set(data) - TOP_KEYS:
        return None
    analyses = data.get("analyses", [])
    if data["status"] == "ok" and not isinstance(analyses, list):
        return None
    return dict(data)


def names_column(term: str, profile: Profile) -> bool:
    t = term.casefold()
    return any(t in c.name.casefold() or c.name.casefold() in t for c in profile.columns)


async def make_plan(deps: llm.TurnDeps, profile: Profile, question: str, context: Sequence[str] = (),
                    limit: int = MAX_ANALYSES, allow_pause: bool = True, fallback: bool = True) -> Planned:
    try:
        data = await llm.complete_json(deps, prompts.plan_messages(profile, question, context, limit),
                                       prompts.PLAN_CONTRACT, parse_plan)
    except llm.LlmError:
        return _default(profile, limit, 0) if fallback else Planned("ok")
    if data["status"] == "off_topic":
        return Planned("off_topic")
    term = " ".join(str(data.get("term") or "").split())[:TERM_MAX_CHARS]
    if data["status"] == "missing_column" and allow_pause and term and profile.of("numeric") \
            and not names_column(term, profile):
        return Planned("missing_column", term=term)
    items, dropped = validate_plan(data.get("analyses"), profile, limit)
    if not items:
        return _default(profile, limit, dropped) if fallback else Planned("ok", dropped=dropped)
    items, topped = top_up(items, profile, limit)
    return Planned("ok", tuple(items), chart_index(items, data.get("chart")), dropped, used_default=topped)


def _default(profile: Profile, limit: int, dropped: int) -> Planned:
    items = default_plan(profile)[:limit]
    return Planned("ok", tuple(items), chart_index(items, None), dropped, used_default=True)


def parse_scope(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("off_topic", "needs_table"):
        return None
    return {"status": data["status"]}


async def check_scope(deps: llm.TurnDeps, question: str, context: Sequence[str] = ()) -> str:
    """off_topic | needs_table, for a message that came with no table and nothing stored. Raises LlmError."""
    data = await llm.complete_json(deps, prompts.scope_messages(question, context), prompts.SCOPE_CONTRACT,
                                   parse_scope)
    return data["status"]
