"""The issues step: every issue of the chosen cycle, read by a bounded tool loop and post-checked in code
(checks.check_issues: ids and completion dates must come from tool output; capped)."""

from __future__ import annotations

from dataclasses import dataclass

import checks
import coverage
import prompts
import records
import replies
from deps import TurnDeps
from discovery import discover
from reader import TurnReader
from records import Cycle, Issue, Team
from render import cycle_label

ISSUE_LOOP_CALLS = 6  # plus one JSON repair = 7
ISSUE_TOOL_CALLS = 20


@dataclass(frozen=True)
class IssuesOutcome:
    issues: tuple[Issue, ...] = ()
    reply: str | None = None      # ends the turn
    extracted: int = 0
    dropped: int = 0
    capped: int = 0
    salvaged: bool = False
    notes: tuple[str, ...] = ()


async def issues_step(deps: TurnDeps, turn: TurnReader, team: Team, cycle: Cycle) -> IssuesOutcome:
    catalog, failure = await discover(deps, "issues")
    if failure is not None:
        return IssuesOutcome(reply=failure)
    tools = catalog.read_tools()
    tracker = coverage.assess("tracker", tools, catalog.source_of)
    if not tracker.ok:
        return IssuesOutcome(reply=f"{replies.NO_TRACKER_TOOL}\n\n{coverage.line(tracker)}")
    label = cycle_label(cycle)
    run = await turn.run_json(deps, tools, prompts.issues_system(label, cycle.id, team.name),
                              f"List the issues of {label} for {team.name}.", ISSUE_LOOP_CALLS, records.parse_issues,
                              ISSUE_TOOL_CALLS)
    tracker = coverage.assess("tracker", tools, catalog.source_of, turn.health.dropped)
    if not tracker.ok:
        return IssuesOutcome(reply=f"{replies.NO_TRACKER_TOOL}\n\n{coverage.line(tracker)}")
    if run.status == "paused":
        return IssuesOutcome(reply=replies.READ_NEEDS_APPROVAL)
    if run.parsed is None:
        return IssuesOutcome(reply="\n\n".join(run.notes or (replies.UNREADABLE_ANSWER,)))
    checked = checks.check_issues(run.parsed, turn.seen_text, deps.config.max_issues)
    if not checked.kept:
        return IssuesOutcome(reply="\n\n".join([replies.NO_ISSUES, *run.notes]))
    notes = run.notes
    if checked.capped:
        cap_note = f"Only the first {deps.config.max_issues} issues are counted; {checked.capped} more were not."
        notes = (*notes, cap_note)
    return IssuesOutcome(checked.kept, None, len(run.parsed), checked.dropped, checked.capped,
                         run.status == "max_iterations", notes)
