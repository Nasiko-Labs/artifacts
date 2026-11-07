"""The team step: read the tracker's teams (and the named team's projects), checked against the tool output;
then resolve the team and project in code by casefold equality. Several teams and none named -> the one
team question. No tracker tool -> the proposal is shown as copyable text only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import coverage
import prompts
import replies
from deps import TurnDeps
from discovery import discover
from llmcall import load_json_object, text_of
from reader import TurnReader
from records import Project, Team
from textcheck import appears_in

TEAM_LOOP_CALLS = 3  # plus one JSON repair


@dataclass(frozen=True)
class TeamRead:
    teams: tuple[Team, ...] = ()
    projects: tuple[Project, ...] = ()
    available: bool = True        # a tracker tool could be used
    notes: tuple[str, ...] = ()
    salvaged: bool = False


def parse_teams(text: str) -> tuple[tuple[Team, ...], tuple[Project, ...]] | None:
    data = load_json_object(text)
    if data is None or not isinstance(data.get("teams"), list):
        return None
    teams = tuple(Team(id=text_of(t.get("id")), name=text_of(t.get("name")), key=text_of(t.get("key")))
                  for t in data["teams"] if isinstance(t, Mapping))
    projects = tuple(Project(id=text_of(p.get("id")), name=text_of(p.get("name")), team_id=text_of(p.get("team_id")))
                     for p in data.get("projects") or () if isinstance(p, Mapping))
    return teams, projects


async def team_step(deps: TurnDeps, reader: TurnReader, hints: tuple[str, ...]) -> TeamRead:
    catalog, failure = await discover(deps, "team")
    if failure is not None:
        return TeamRead(available=False, notes=(failure,))
    tools = catalog.read_tools("tracker")
    if not coverage.assess("tracker", tools).ok:
        return TeamRead(available=False, notes=(replies.NO_TRACKER_NOTE,))
    run = await reader.run_json(deps, tools, prompts.team_system(hints), "List the teams.", TEAM_LOOP_CALLS,
                                parse_teams)
    status = coverage.assess("tracker", tools, reader.health.dropped)
    if not status.ok:
        return TeamRead(available=False, notes=(coverage.line(status), replies.NO_TRACKER_NOTE))
    if run.parsed is None:
        return TeamRead(available=False, notes=(*run.notes, replies.NO_TRACKER_NOTE))
    teams, projects = run.parsed
    seen = reader.seen_text
    return TeamRead(teams=tuple(t for t in teams if t.id and t.name and appears_in(t.id, seen)
                                and appears_in(t.name, seen)),
                    projects=tuple(p for p in projects if p.id and p.name and appears_in(p.id, seen)
                                   and appears_in(p.name, seen)),
                    notes=run.notes, salvaged=run.salvaged)


def resolve_team(teams: Sequence[Team], hints: Sequence[str]) -> Team | None:
    """The team named by a hint (name or key, casefold), else the only team, else None."""
    for hint in hints:
        wanted = hint.strip().casefold()
        for team in teams:
            if wanted and wanted in (team.name.casefold(), team.key.casefold()):
                return team
    return teams[0] if len(teams) == 1 else None


def resolve_project(projects: Sequence[Project], team: Team | None, named: str, spec_title: str) -> Project:
    """The existing project the user named (casefold), else a new project named after the spec title."""
    wanted = named.strip().casefold()
    own = [p for p in projects if not p.team_id or team is None or p.team_id == team.id]
    for project in own:
        if wanted and project.name.casefold() == wanted:
            return project
    return Project(name=(named.strip() or spec_title.strip() or "New project")[:80],
                   team_id=team.id if team else "")
