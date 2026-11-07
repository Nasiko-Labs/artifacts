"""Resuming a paused task: the team answer, the picker (edit rounds, Cancel, or which issues get cards), and
the create walk (cards.py). Everything needed rides on the task's metadata, so any replica resumes it.

    team? -> proposal -> [picker] -+- edit text -> re-render -> [picker] (at most 3 rounds)
                                   +- Cancel / nothing picked -> done, nothing written
                                   +- picked -> cards.start (one card per write)"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import approval
import cards
import edits
import flow
import outcome
import replies
from deps import TurnDeps
from outcome import METADATA_KEY, Outcome, View
from records import issues_to, to_dict
from steps_team import resolve_project


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], text: str) -> Outcome:
    state = metadata.get(METADATA_KEY)
    if not isinstance(state, Mapping):
        return Outcome("complete", replies.NOT_WAITING)
    phase = state.get("phase")
    if phase == "team":
        return await flow.resume_team(deps, state, text)
    if phase == "pick":
        return await resume_pick(deps, dict(state), text)
    if phase == "card":
        return await cards.resume_card(deps, dict(state), text)
    if phase == "gateway":
        return await cards.resume_gateway(deps, dict(state), text)
    return Outcome("complete", replies.NOT_WAITING)


async def resume_pick(deps: TurnDeps, state: dict[str, Any], text: str) -> Outcome:
    offered = {str(k): int(v) for k, v in dict(state.get("offered") or {}).items()}
    picked = approval.read_picker(text, offered)
    if picked.cancel:
        return outcome.complete(f"{replies.CANCELLED} Nothing was created.", state, replies.CANCELLED)
    if picked.text:
        return await _edit(deps, state, picked.text, bool(picked.ids))
    if not picked.ids:
        return outcome.complete(replies.NOTHING_SELECTED, state, replies.NOTHING_SELECTED)
    return await cards.start(deps, state, picked.ids)


async def _edit(deps: TurnDeps, state: dict[str, Any], text: str, had_picks: bool) -> Outcome:
    rounds = int(state.get("rounds") or 0)
    if rounds >= deps.config.max_edit_rounds:
        lead = replies.EDITS_EXHAUSTED.format(rounds=deps.config.max_edit_rounds)
        return flow.show_proposal(deps, state, True, lead)
    view = View(state)
    if edits.commands_only(text):
        result = edits.apply_commands(view.issues, text)
    else:
        result = await edits.revise(deps, view.issues, view.spec.text, text)
    if result.off_topic:
        return flow.show_proposal(deps, state, True, "That isn't an edit of this list, so nothing changed.")
    lead = list(result.notes)
    state = {**state, "issues": issues_to(result.issues), "rounds": rounds + 1}
    if result.team:
        state, note = _change_team(state, result.team)
        lead.append(note)
    if result.project:
        team = View(state).team
        project = resolve_project(view.projects, team, result.project, view.spec.title)
        state = {**state, "project": to_dict(project)}
        lead.append(f'Project set to {"existing" if project.id else "new"} project "{project.name}".')
    if had_picks:
        lead.append("I applied the edit first; pick the issues again.")
    if not View(state).issues:
        return outcome.complete("Every issue was dropped, so nothing will be created.", state, "Nothing left.")
    return flow.show_proposal(deps, state, True, " ".join(lead))


def _change_team(state: dict[str, Any], name: str) -> tuple[dict[str, Any], str]:
    view = View(state)
    wanted = name.strip().casefold()
    team = next((t for t in view.teams if wanted in (t.name.casefold(), t.key.casefold())), None)
    if team is None:
        return state, f'There is no team called "{name}"; the team is unchanged.'
    cleared = [{**i, "exists": "", "exists_url": "", "status": "proposed"} for i in state.get("issues") or ()]
    note = (f"Team set to {team.name}. The duplicate check was for the earlier team, so no issue is marked as "
            "existing now.")
    return {**state, "team": to_dict(team), "issues": cleared}, note
