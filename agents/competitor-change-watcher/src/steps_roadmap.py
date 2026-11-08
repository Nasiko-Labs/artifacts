"""The roadmap lookup: one issue-tracker tool loop over the top changes (their keywords as text searches),
then the `{"issues": [...]}` contract, read once more from the tool output if the loop's answer is not valid.
Every issue must carry an id and a title that appear in this turn's tool output; anything else is dropped."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import coverage
import prompts
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway
from llm import LlmError, call, load
from nasiko_mcp import McpGatewayError
from records import Change
from tool_health import ToolHealth
from tool_loop import run_tool_loop
from words import norm

log = logging.getLogger("change_watcher.roadmap")
MAX_EVIDENCE_CHARS = 24_000
CALL_TIMEOUT_SECS = 90.0


@dataclass(frozen=True)
class Roadmap:
    issues: tuple[dict, ...] = ()
    line: str = coverage.CHECKED
    checked: bool = False
    dropped: int = 0


def _issues(raw: Any) -> list[Mapping[str, Any]] | None:
    rows = raw.get("issues") if isinstance(raw, Mapping) else None
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else None


def verified(rows: Sequence[Mapping[str, Any]], seen: str) -> tuple[tuple[dict, ...], int]:
    plain = norm(seen)
    kept: dict[str, dict] = {}
    for row in rows:
        ident, title = str(row.get("id") or "").strip(), " ".join(str(row.get("title") or "").split())
        if not ident or ident not in seen or not title or norm(title) not in plain:
            continue
        url, state = str(row.get("url") or "").strip(), " ".join(str(row.get("state") or "").split())
        kept.setdefault(ident, {"id": ident, "title": title, "url": url if url and url in seen else "",
                                "state": state if state and norm(state) in plain else "unknown"})
    return tuple(kept.values()), len(rows) - len(kept)


def _changes_message(changes: Sequence[Change]) -> str:
    rows = [{"n": n, "competitor": c.competitor, "what": c.what, "keywords": list(c.keywords)}
            for n, c in enumerate(changes, start=1)]
    return "Competitor changes to look up on our roadmap:\n" + prompts.fence_changes(rows)


async def lookup(deps: TurnDeps, catalog: Catalog, changes: Sequence[Change], team: str | None) -> Roadmap:
    tools = catalog.tracker_tools()
    if not tools:
        return Roadmap(line=coverage.MISSING)
    iterations = min(deps.config.roadmap_max_iterations, deps.budget.max_calls - deps.budget.calls)
    if iterations < 1 or deps.remaining() < CALL_TIMEOUT_SECS / 3:
        deps.budget.exhausted = True
        return Roadmap(line=coverage.STEP_LIMIT)
    health = ToolHealth(deps.gateway)
    fenced = FencedGateway(health, deps.config.roadmap_max_tool_calls)
    messages = ({"role": "system", "content": prompts.roadmap_system(team)},
                {"role": "user", "content": _changes_message(changes)})
    try:
        result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                     max_iterations=iterations, is_offered=health.is_offered,
                                     timeout=max(1.0, min(CALL_TIMEOUT_SECS, deps.remaining())))
    except McpGatewayError as e:
        log.warning("roadmap loop failed: %s", e)
        return Roadmap(line=coverage.UNREACHABLE)
    deps.budget.calls += sum(1 for m in result.messages if m.get("role") == "assistant")
    gap = coverage.line(tools, health.dropped)
    if gap:
        return Roadmap(line=gap)
    rows = _issues(load(result.text))
    if rows is None:
        rows = await _evidence_read(deps, team, fenced.seen_text)
    issues, dropped = verified(rows or [], fenced.seen_text)
    return Roadmap(issues=issues, checked=True, dropped=dropped)


async def _evidence_read(deps: TurnDeps, team: str | None, seen: str) -> list[Mapping[str, Any]] | None:
    """One tool-less repair: the contract over everything the tools returned."""
    if not seen:
        return None
    messages = ({"role": "system", "content": prompts.roadmap_system(team)},
                {"role": "user", "content": prompts.fence(seen[:MAX_EVIDENCE_CHARS])})
    try:
        return _issues(load(await call(deps, messages, json_mode=True)))
    except LlmError as e:
        log.warning("roadmap evidence read failed: %s", e)
        return None
