"""One analysis: diff every block in code, classify the new changelog entries, turn price facts into changes,
look the top changes up on the roadmap, map and order them in code, and build the envelope. Returns the
envelope and the replaced snapshots."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import coverage
import discovery
import envelope
import mapping
import snapshot
from classify import Classified, classify
from deps import TurnDeps
from diff import BlockDiff, diff_all
from nasiko_mcp import McpGatewayError
from records import Block, Change, PriceFact
from steps_roadmap import Roadmap, lookup
from words import tokens

log = logging.getLogger("change_watcher.analyse")
PRICE_KEYWORDS = ("pricing", "price", "plan")


@dataclass(frozen=True)
class Intake:
    blocks: tuple[Block, ...]
    team: str | None = None
    paste_cut: bool = False


def price_text(f: PriceFact) -> str:
    if f.kind == "changed":
        pct = f" ({f.delta_pct:+g}%)" if f.delta_pct is not None else ""
        return f"{f.plan} plan {f.old} → {f.new}{pct}"
    if f.kind == "added":
        return f"New plan {f.plan} at {f.new}"
    if f.kind == "removed":
        return f"{f.plan} plan removed (was {f.old})"
    return f"{f.plan} plan limit {f.old or 'none'} → {f.new or 'none'}"


def price_change(block: Block, fact: PriceFact) -> Change:
    unit = tokens(" ".join(x for x in (fact.old, fact.new) if x)) if fact.kind == "limit" else frozenset()
    words = (*PRICE_KEYWORDS, *sorted(tokens(fact.plan)), *sorted(w for w in unit if not w.isdigit()))
    return Change(competitor=block.competitor, source=block.source, category="pricing", what=price_text(fact),
                  quote=fact.line, keywords=tuple(dict.fromkeys(words)), price_fact=fact)


def _new_entries(diffs: Sequence[BlockDiff]):
    found = [e for d in diffs for e in d.new]
    dated = sorted((e for e in found if e.date), key=lambda e: e.date, reverse=True)
    return [*dated, *(e for e in found if not e.date)]


async def _roadmap(deps: TurnDeps, changes: Sequence[Change], team: str | None) -> Roadmap:
    if not changes:
        return Roadmap(line=coverage.NOTHING_NEW, checked=True)
    if deps.gateway is None:
        return Roadmap(line=coverage.NO_GATEWAY)
    try:
        catalog = await discovery.discover(deps)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", e)
        return Roadmap(line=coverage.UNREACHABLE)
    return await lookup(deps, catalog, changes, team)


def _checks(diffs: Sequence[BlockDiff], found: int, cls: Classified, road: Roadmap, deps: TurnDeps,
            intake: Intake) -> dict:
    return {"blocks": len(diffs), "entries": sum(d.total for d in diffs if not d.block.is_pricing), "new": found,
            "capped": max(0, found - deps.config.max_entries), "classified": len(cls.changes),
            "dropped": cls.dropped, "unclassified": cls.unclassified, "salvage": deps.budget.exhausted,
            "roadmap_checked": road.checked, "roadmap_dropped": road.dropped, "paste_cut": intake.paste_cut,
            "unnamed": any(not d.block.named for d in diffs), "pricing_failed": any(d.pricing_failed for d in diffs),
            "unbaselined": sum(len(d.unbaselined) for d in diffs), "llm_calls": deps.budget.calls}


async def run(deps: TurnDeps, intake: Intake, snapshots: Mapping[str, str]) -> tuple[dict, dict]:
    diffs = diff_all(intake.blocks, snapshots, deps.today, deps.config.recent_days)
    found = _new_entries(diffs)
    cls = await classify(deps, found[:deps.config.max_entries]) if found else Classified(())
    changes = [*(price_change(d.block, f) for d in diffs for f in d.price_facts), *cls.changes]
    road = await _roadmap(deps, mapping.ordered(changes)[:deps.config.roadmap_top], intake.team)
    final = mapping.ordered(mapping.mapped(changes, road.issues, deps.config))
    checks = _checks(diffs, len(found), cls, road, deps, intake)
    env = envelope.build(final, diffs, road, checks, deps.today, deps.config.recent_days)
    return env, snapshot.replaced(snapshots, intake.blocks)
