"""The ship step: collect release evidence for the bug's fix versions (code host + tracker version), then
decide in code (ship.decide). No code-host tool, or every one unavailable → `unverified`, no drafts."""

from __future__ import annotations

import coverage
import prompts
import records
import replies
import ship
from deps import TurnDeps
from discovery import discover
from reader import TurnReader
from records import Bug, ShipVerdict

SHIP_LOOP_CALLS = 3  # plus one JSON repair = 4
NO_CODE_HOST = "no code-host tool is available to check the release"


async def ship_step(deps: TurnDeps, turn: TurnReader, bug: Bug) -> tuple[ShipVerdict, tuple[str, ...]]:
    """The verdict and the notes to show with it (coverage lines, step limit)."""
    catalog, failure = await discover(deps, "ship")
    if failure is not None:
        return ship.unverified(NO_CODE_HOST), (failure,)
    tools = catalog.read_tools()
    code_host = coverage.assess("code_host", tools, catalog.source_of)
    if not code_host.ok:  # no LLM call: without a code host the release cannot be confirmed
        return ship.unverified(NO_CODE_HOST), (coverage.line(code_host),)
    system = prompts.ship_system(deps.today, bug.key, bug.fix_versions)
    run = await turn.run_json(deps, tools, system, f"Collect the release evidence for {bug.key}.", SHIP_LOOP_CALLS,
                              records.parse_ship)
    code_host = coverage.assess("code_host", tools, catalog.source_of, turn.health.dropped)
    if not code_host.ok:
        return ship.unverified(NO_CODE_HOST), (coverage.line(code_host),)
    if run.status == "paused":
        return ship.unverified("a release read needs approval"), (replies.READ_NEEDS_APPROVAL,)
    if run.parsed is None:
        return ship.unverified("I couldn't read the release evidence"), run.notes
    return ship.decide(bug.fix_versions, run.parsed, turn.seen_text, deps.now), run.notes
