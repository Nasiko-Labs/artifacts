"""The tracker step: one bounded tool loop for every commitment together, post-checked per field
(evidence.check_tracker). A tracker that is dead for this user ends the turn with a reply naming it."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import coverage
import evidence
import prompts
from commitments import Commitment, listing
from deps import TurnDeps
from discovery import RECOVER_META, discover
from quarter import Quarter
from reader import Reader

log = logging.getLogger("commitment_audit.tracker")

LOOP_CALLS = 7  # plus one repair = 8


@dataclass(frozen=True)
class TrackerOutcome:
    reply: str | None
    facts: dict[str, evidence.TrackerFacts] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


async def collect(deps: TurnDeps, reader: Reader, items: Sequence[Commitment], quarter: Quarter) -> TrackerOutcome:
    catalog, failure = await discover(deps, "tracker")
    if failure is not None:
        return TrackerOutcome(failure)
    tools = catalog.read_tools(("tracker",))
    if not tools:
        return TrackerOutcome(prompts.NO_TRACKER_TOOL)
    user = prompts.fence_items(json.dumps(listing(items), ensure_ascii=False, indent=1))
    step = await reader.run(deps, prompts.tracker_system(quarter, deps.today), user, tools, LOOP_CALLS,
                            lambda text: evidence.parse_items(text, "items"))
    covered = coverage.assess("tracker", tools, reader.health.dropped, frozenset({RECOVER_META}))
    if covered.state == "unavailable":
        return TrackerOutcome(prompts.tracker_unavailable(covered.reason))
    if step.paused:
        return TrackerOutcome(prompts.READ_NEEDS_APPROVAL)
    facts = evidence.check_tracker(step.parsed or [], items, reader.seen_text)
    log.info("tracker facts kept for %d of %d commitments", len(facts), len(items))
    return TrackerOutcome(None, facts, (prompts.STEP_LIMIT_NOTE,) if step.hit_limit else ())
