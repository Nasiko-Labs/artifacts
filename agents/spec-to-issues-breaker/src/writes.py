"""The write side of one card: the exact approval built in code from checked records, applying a card edit,
running the one guarded write (sender.py), and recording what the tool returned (identifier, link, project
id read from the write's own output)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import render
from deps import TurnDeps
from discovery import discover
from outcome import View
from records import Project, issues_to, to_dict
from send_guard import Approval
from sender import WriteResult, resume_write, write_once
from split import MAX_TITLE_CHARS

ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def approval_for(view: View, step: Any) -> Approval:
    team = view.team
    base = {"team_id": team.id, "team_key": team.key, "team_name": team.name}
    if step == "project":
        return Approval(kind="project", tool=str(view.get("project_tool")), local_id=0, title=view.project.name,
                        description="", **base)
    issue = next(i for i in view.issues if i.local_id == int(step))
    by_id = {i.local_id: i for i in view.issues}
    field = bool(view.get("estimate_field"))
    project = view.project if view.project and view.project.id else None
    return Approval(kind="issue", tool=str(view.get("issue_tool")), local_id=issue.local_id, title=issue.title,
                    description=render.issue_text(issue, by_id, estimate_footer=not field),
                    project_id=project.id if project else "", project_name=project.name if project else "",
                    estimate=issue.estimate if field else None, **base)


def edited(current: Approval, title: str, description: str) -> Approval:
    return replace(current, title=(title or current.title)[:MAX_TITLE_CHARS],
                   description=description if description and current.kind == "issue" else current.description)


async def create(deps: TurnDeps, state: Mapping[str, Any]) -> WriteResult:
    current = Approval.from_dict(state.get("approval"))
    tool = await _tool(deps, current)
    if tool is None:
        return WriteResult("not_done", "the creating tool is no longer available")
    return await write_once(deps, tool, current)


async def resume(deps: TurnDeps, state: Mapping[str, Any], answer: str) -> WriteResult:
    current = Approval.from_dict(state.get("approval"))
    tool = await _tool(deps, current)
    if tool is None or not state.get("loop"):
        return WriteResult("not_done", "the creating tool is no longer available")
    return await resume_write(deps, tool, current, dict(state["loop"]), answer)


async def _tool(deps: TurnDeps, current: Approval | None):
    if current is None:
        return None
    catalog, _ = await discover(deps, "write")
    return next((t for t in catalog.tools if t.name == current.tool), None) if catalog else None


def _current_step(state: Mapping[str, Any]) -> Any:
    queue = list(state.get("queue") or ())
    index = int(state.get("index") or 0)
    return queue[index] if index < len(queue) else None


def mark_current(state: dict[str, Any], status: str, **changes: Any) -> dict[str, Any]:
    step = _current_step(state)
    if step == "project":
        line = "The new project was not created; the issues go without a project." if status != "created" else ""
        return {**state, "project": None, "project_line": line}
    issues = [replace(i, status=status, **changes) if i.local_id == int(step) else i for i in View(state).issues]
    return {**state, "issues": issues_to(issues)}


def record_done(state: dict[str, Any], output: str) -> dict[str, Any]:
    step = _current_step(state)
    view = View(state)
    if step == "project":
        project_id = project_id_from(output, view.project.name)
        if not project_id:
            return {**state, "project": None,
                    "project_line": f'Project "{view.project.name}" was created, but its id was not in the reply, '
                                    "so the issues go without a project."}
        project = Project(name=view.project.name, id=project_id, team_id=view.team.id)
        return {**state, "project": to_dict(project), "project_line": f'Created project "{project.name}".'}
    identifier, url = created_ref(output)
    return mark_current(state, "created", identifier=identifier, url=url)


def created_ref(output: str) -> tuple[str, str]:
    """(identifier, url) from the create tool's own output; empty when it did not say."""
    key = ISSUE_KEY.search(output or "")
    urls = [u.rstrip(".,;") for u in _URL.findall(output or "")]
    url = next((u for u in urls if key and key.group(0) in u), urls[0] if urls else "")
    return (key.group(0) if key else "", url)


def project_id_from(output: str, name: str) -> str:
    """The new project's id from the write's own output: the `id` next to its name, else the first `id`."""
    try:
        data = json.loads(output)
    except ValueError:
        return ""
    ids = list(_ids(data, name.casefold()))
    named = [i for i, matched in ids if matched]
    return (named or [i for i, _ in ids] or [""])[0]


def _ids(value: Any, name: str):
    if isinstance(value, Mapping):
        if isinstance(value.get("id"), (str, int)) and str(value.get("id")):
            yield str(value["id"]), str(value.get("name") or "").casefold() == name
        for v in value.values():
            yield from _ids(v, name)
    elif isinstance(value, list):
        for v in value:
            yield from _ids(v, name)
