"""Turn 1 after the spec is read: team -> split (chunked) -> estimates and dependencies (code) -> duplicate
check -> proposal, ending on the picker pause. Several teams and none named -> the team question first; its
answer continues here. Nothing is written in this module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from typing import Any

import approval
import outcome
import render
import replies
from deps import TurnDeps
from discovery import discover
from dupcheck import dup_check
from outcome import Outcome, View
from proposal import clean_dependencies, with_estimates
from reader import TurnReader
from records import Spec, issues_to, to_dict
from request import Ask
from send_guard import name_tokens
from split import split_spec
from steps_team import resolve_project, resolve_team, team_step


async def begin(deps: TurnDeps, reader: TurnReader, ask: Ask, spec: Spec, notes: tuple[str, ...],
                salvaged: bool) -> Outcome:
    read = await team_step(deps, reader, ask.team_hints)
    team = resolve_team(read.teams, ask.team_hints) if read.available else None
    state: dict[str, Any] = {
        "spec": to_dict(spec), "teams": [to_dict(t) for t in read.teams], "projects": [to_dict(p) for p in read.projects],
        "team": to_dict(team) if team else None, "project_hint": ask.project, "tracker": read.available,
        "json_mode": ask.wants_json, "notes": [*notes, *read.notes], "rounds": 0,
        "checks": {"sources_planned": 2, "sources_read": 2 if read.available else 1,
                   "salvaged": salvaged or read.salvaged},
    }
    if read.available and team is None and len(read.teams) > 1:
        hitl, _ = approval.team_question(read.teams, deps.config.max_teams_offered)
        lead = "I found several teams and couldn't tell which one you meant."
        return outcome.pause(f"{lead} {approval.TEAM_HEADER}", {**state, "phase": "team"}, hitl)
    if read.available and not read.teams:
        state = {**state, "tracker": False, "notes": [*state["notes"], "I found no team in the tracker.",
                                                      replies.NO_TRACKER_NOTE]}
    return await propose(deps, reader, state)


async def resume_team(deps: TurnDeps, state: Mapping[str, Any], answer: str) -> Outcome:
    view = View(state)
    team = approval.chosen_team(answer, view.teams)
    if team is None:
        hitl, _ = approval.team_question(view.teams, deps.config.max_teams_offered)
        return outcome.pause(replies.UNKNOWN_TEAM, state, hitl)
    return await propose(deps, TurnReader(deps), {**state, "team": to_dict(team)})


async def propose(deps: TurnDeps, reader: TurnReader, state: dict[str, Any]) -> Outcome:
    view = View(state)
    split = await split_spec(deps, view.spec.text)
    issues, cycle_notes = clean_dependencies(with_estimates(split.issues))
    notes = [*view.notes, *cycle_notes, *_split_notes(split, view.spec)]
    team = view.team
    project = resolve_project(view.projects, team, str(view.get("project_hint") or ""), view.spec.title)
    dup_ok, salvaged = True, False
    if team is not None and view.get("tracker"):
        dup = await dup_check(deps, reader, team, issues)
        issues, dup_ok, salvaged = dup.issues, dup.ok, dup.salvaged
        notes += list(dup.notes)
    tools = await _write_tools(deps) if team is not None and view.get("tracker") else {}
    if team is not None and view.get("tracker") and not tools.get("issue_tool"):
        notes.append(replies.NO_CREATE_TOOL_NOTE)
    can_create = bool(tools.get("issue_tool")) and bool(issues)
    checks = replace(view.checks, capped=split.capped > 0 or split.limit_hit or split.chunks_read < split.chunks,
                     extracted=split.extracted, dropped=split.dropped, unclear=any(i.estimate is None for i in issues),
                     dropped_lines=split.dropped_lines > 0, salvaged=view.checks.salvaged or salvaged or split.limit_hit,
                     sources_read=view.checks.sources_read if dup_ok else 1)
    state = {**state, **tools, "issues": issues_to(issues), "project": to_dict(project) if team else None,
             "notes": notes, "checks": asdict(checks), "phase": "pick"}
    if not issues:
        return outcome.complete("\n\n".join([replies.NOTHING_IN_SPEC, *notes]), state, replies.NOTHING_IN_SPEC)
    return show_proposal(deps, state, can_create)


def show_proposal(deps: TurnDeps, state: Mapping[str, Any], can_create: bool, lead: str = "") -> Outcome:
    view = View(state)
    text = render.proposal(view.spec, view.team, view.project, view.issues, view.notes, can_create)
    text = f"{lead}\n\n{text}" if lead else text
    if not can_create:
        return outcome.complete(text, state, outcome.summary_of(state))
    allow_edit = int(view.get("rounds") or 0) < deps.config.max_edit_rounds
    hitl, offered = approval.picker(view.issues, allow_edit)
    return outcome.pause(text, {**state, "offered": offered}, hitl)


async def _write_tools(deps: TurnDeps) -> dict[str, Any]:
    catalog, failure = await discover(deps, "write")
    if catalog is None or failure is not None:
        return {}
    issue_tool, project_tool = catalog.create_tool("issue"), catalog.create_tool("project")
    if issue_tool is None:
        return {}
    props = issue_tool.input_schema.get("properties") or {}
    return {"issue_tool": issue_tool.name, "project_tool": project_tool.name if project_tool else "",
            "estimate_field": any("estimate" in name_tokens(str(k)) for k in props)}


def _split_notes(split, spec: Spec) -> list[str]:
    notes = []
    if spec.cut_chars:
        notes.append(f"The spec is longer than I read: the last {spec.cut_chars} characters were not covered.")
    if split.chunks > 1:
        notes.append(f"The spec was read in {split.chunks} parts; {split.chunks_read} were covered.")
    if split.dropped:
        notes.append(f"{split.dropped} issue(s) were left out because their quote was not word for word in the spec.")
    if split.dropped_lines:
        notes.append(f"{split.dropped_lines} acceptance line(s) were left out: not word for word in the spec.")
    if split.capped:
        notes.append(f"{split.capped} more issue(s) were found than I show; ask for the rest after these.")
    if split.limit_hit or split.unreadable:
        notes.append(replies.STEP_LIMIT_NOTE)
    return notes
