"""The cycle step: the team and the cycle, read from the tracker by a bounded tool loop; the team and the
cycle are then picked in code ("last" = the latest cycle that ended before today, "current" = the one
running today). Without a tracker tool, one tool-less call only tells a retro request from anything else."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import checks
import coverage
import prompts
import records
import replies
import smalltalk
from dates import instant
from deps import TurnDeps
from discovery import discover
from reader import TurnReader, ask_once
from records import Cycle, Team
from request import Request

CYCLE_LOOP_CALLS = 3  # plus one JSON repair = 4
CYCLE_TOOL_CALLS = 8


@dataclass(frozen=True)
class CycleOutcome:
    """Either a reply that ends the turn, teams to ask about, or the team and cycle to continue with."""

    reply: str | None = None
    team: Team | None = None
    cycle: Cycle | None = None
    teams_to_pick: tuple[Team, ...] = ()
    notes: tuple[str, ...] = ()
    salvaged: bool = False
    dropped: int = 0


async def cycle_step(deps: TurnDeps, turn: TurnReader, query: str, req: Request) -> CycleOutcome:
    catalog, failure = await discover(deps, "cycle")
    if failure is not None:
        if failure == replies.NO_TRACE_CONTEXT:
            return CycleOutcome(failure)
        return CycleOutcome(await no_tracker_reply(deps, query, failure))
    tools = catalog.read_tools()
    if not coverage.assess("tracker", tools, catalog.source_of).ok:
        return CycleOutcome(await no_tracker_reply(deps, query, replies.NO_TRACKER_TOOL))
    run = await turn.run_json(deps, tools, prompts.cycle_system(deps.today, req.team), query, CYCLE_LOOP_CALLS,
                              records.parse_cycles, CYCLE_TOOL_CALLS)
    tracker = coverage.assess("tracker", tools, catalog.source_of, turn.health.dropped)
    if not tracker.ok:
        return CycleOutcome(f"{replies.NO_TRACKER_TOOL}\n\n{coverage.line(tracker)}")
    if run.status == "paused":
        return CycleOutcome(replies.READ_NEEDS_APPROVAL)
    answer = run.parsed
    if answer is None:
        return CycleOutcome(replies.STEP_LIMIT_NOTE if run.status in ("limit", "max_iterations")
                            else replies.UNREADABLE_ANSWER)
    if answer.status == "off_topic":
        return CycleOutcome(smalltalk.OFF_TOPIC_REPLY)
    if answer.status == "no_tracker_tool":
        return CycleOutcome(replies.NO_TRACKER_TOOL)
    return _pick(answer, turn.seen_text, req, deps, run.notes, run.status == "max_iterations")


async def no_tracker_reply(deps: TurnDeps, query: str, missing: str) -> str:
    """No tracker (or no gateway): decline anything that is not a retro request, else say what is missing."""
    messages = ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM}, {"role": "user", "content": query})
    try:
        verdict = await ask_once(deps, messages)
    except Exception:  # noqa: BLE001 - the scope check is best effort; the honest "missing" reply stands
        return missing
    return smalltalk.OFF_TOPIC_REPLY if prompts.OFF_TOPIC in verdict.upper() else missing


def _pick(answer: records.CycleAnswer, seen_text: str, req: Request, deps: TurnDeps, notes: tuple[str, ...],
          salvaged: bool) -> CycleOutcome:
    teams_checked = checks.check_teams(answer.teams, seen_text)
    cycles_checked = checks.check_cycles(answer.cycles, seen_text)
    dropped = teams_checked.dropped + cycles_checked.dropped
    teams = teams_checked.kept
    team = resolve_team(teams, req.team)
    if team is None:
        if len(teams) > 1:
            return CycleOutcome(teams_to_pick=teams[:deps.config.max_teams_offered], notes=notes)
        return CycleOutcome("\n\n".join([replies.NO_TEAM, *notes]))
    own = tuple(c for c in cycles_checked.kept if not c.team_id or c.team_id == team.id)
    cycle = pick_cycle(own, req.cycle, deps.now)
    if cycle is None:
        return CycleOutcome("\n\n".join([_no_cycle(req.cycle, team), *notes]))
    return CycleOutcome(team=team, cycle=cycle, notes=notes, salvaged=salvaged, dropped=dropped)


def resolve_team(teams: Sequence[Team], named: str) -> Team | None:
    """The named team (by name or id, case-insensitive), else the only team, else None."""
    if named:
        wanted = named.casefold()
        exact = next((t for t in teams if wanted in (t.name.casefold(), t.id.casefold())), None)
        return exact or next((t for t in teams if t.name.casefold().startswith(wanted)), None)
    return teams[0] if len(teams) == 1 else None


def pick_cycle(cycles: Sequence[Cycle], ref: str, now: datetime) -> Cycle | None:
    dated = [(c, instant(c.starts_at), instant(c.ends_at, end_of_day=True)) for c in cycles]
    dated = [(c, s, e) for c, s, e in dated if s and e]
    if ref == "current":
        return next((c for c, s, e in dated if s <= now <= e), None)
    if ref.startswith("number:"):
        wanted = ref.split(":", 1)[1]
        return next((c for c, _, _ in dated if c.number.split(".")[0] == wanted), None)
    if ref.startswith("name:"):
        wanted = ref.split(":", 1)[1].casefold()
        return next((c for c, _, _ in dated if c.name and c.name.casefold() == wanted), None)
    finished = [(c, e) for c, _, e in dated if e.date() < now.date()]
    return max(finished, key=lambda pair: pair[1])[0] if finished else None


def _no_cycle(ref: str, team: Team) -> str:
    if ref == "last":
        return replies.NO_FINISHED_CYCLE.format(team=team.name)
    if ref == "current":
        return replies.NO_CURRENT_CYCLE.format(team=team.name)
    return replies.NO_CYCLE
