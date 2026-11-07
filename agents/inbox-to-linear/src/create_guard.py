"""Code-enforced gate on the one issue-creating call an approval allows.

The create card shows the exact issue; the model then maps it onto the tracker tool's arguments. This
checks that call against what was approved: the same tool, the title and description verbatim, the
approved team (by id or name), labels only from the approved set, a priority only when one was approved,
a due date only when one was approved and equal to it, no assignee, and no other free text. Case and
whitespace are ignored. Pure: decisions only, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import send_guard
from models import IssueDraft

_WHITESPACE = re.compile(r"\s+")
_TITLE_KEYS = ("title", "summary")
_DESCRIPTION_KEYS = ("description", "body", "content", "text", "markdown")
_TEAM_KEYS = ("team",)
_PROJECT_KEYS = ("project",)
_LABEL_KEYS = ("label", "labels", "tag", "tags")
_PRIORITY_KEYS = ("priority",)
_DUE_KEYS = ("due",)
_ASSIGNEE_KEYS = ("assignee", "assign", "owner")
_PRIORITY_VALUES = {"urgent": {"1", "urgent", "highest", "critical", "p0", "p1"},
                    "low": {"4", "low", "lowest", "p3", "p4"}}


@dataclass(frozen=True)
class CreateApproval:
    tool: str
    issue: IssueDraft

    def to_dict(self) -> dict:
        return {"tool": self.tool, "issue": self.issue.to_dict()}

    @staticmethod
    def from_dict(data: object) -> CreateApproval | None:
        if not isinstance(data, Mapping) or not data.get("tool") or not isinstance(data.get("issue"), Mapping):
            return None
        return CreateApproval(tool=str(data["tool"]), issue=IssueDraft.from_dict(data["issue"]))


def check(name: str, arguments: Mapping[str, Any], approval: CreateApproval | None) -> str | None:
    """None when the call is exactly the approved create; else the reason it is refused."""
    if not send_guard.is_write_tool(name):
        return None
    if approval is None:
        return "Refused: creating an issue needs the user's approval first. Nothing was created."
    if name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved creating with {approval.tool}, not {name}."
    fields = _scalars(arguments)
    return (_text_problem(fields, approval.issue) or _team_problem(fields, approval.issue)
            or _label_problem(fields, approval.issue) or _optional_problem(fields, approval.issue))


def _text_problem(fields: list[tuple[str, str]], issue: IssueDraft) -> str | None:
    title, body = _norm(issue.title), _norm(issue.description)
    titles = [_norm(v) for k, v in fields if _key_is(k, _TITLE_KEYS)]
    bodies = [_norm(v) for k, v in fields if _key_is(k, _DESCRIPTION_KEYS)]
    if not titles or any(t != title for t in titles):
        return "Refused: the title must be exactly the approved title, verbatim."
    if not bodies or any(b != body for b in bodies):
        return "Refused: the description must be exactly the approved description, verbatim."
    allowed = {title, body, *_names(issue)}
    stray = sorted({k or "?" for k, v in fields if " " in v.strip() and _norm(v) not in allowed})
    if stray:
        return f"Refused: this call carries text the user did not approve (in {', '.join(stray)})."
    if any(_key_is(k, _ASSIGNEE_KEYS) and v.strip() for k, v in fields):
        return "Refused: issues are created unassigned; leave the assignee out."
    return None


def _team_problem(fields: list[tuple[str, str]], issue: IssueDraft) -> str | None:
    team_ids = {_norm(x) for x in (issue.team.id, issue.team.name) if x} if issue.team else set()
    project_ids = {_norm(x) for x in (issue.project.id, issue.project.name) if x} if issue.project else set()
    teams = [_norm(v) for k, v in fields if _key_is(k, _TEAM_KEYS)]
    projects = [_norm(v) for k, v in fields if _key_is(k, _PROJECT_KEYS)]
    if team_ids and not teams and not (set(projects) & team_ids):
        return "Refused: the approved team is missing from the call."
    if any(t not in team_ids for t in teams):
        return "Refused: the team in this call is not the approved team."
    if any(p not in project_ids | team_ids for p in projects):
        return "Refused: the project in this call is not the approved project."
    return None


def _label_problem(fields: list[tuple[str, str]], issue: IssueDraft) -> str | None:
    approved = {_norm(x) for label in issue.labels for x in (label.id, label.name) if x}
    labels = [_norm(v) for k, v in fields if _key_is(k, _LABEL_KEYS) and v.strip()]
    if any(label not in approved for label in labels):
        return "Refused: only the approved labels may be set."
    return None


def _optional_problem(fields: list[tuple[str, str]], issue: IssueDraft) -> str | None:
    priorities = [_norm(v) for k, v in fields if _key_is(k, _PRIORITY_KEYS) and _norm(v) not in ("", "0", "none")]
    if priorities and (issue.priority is None or any(p not in _PRIORITY_VALUES[issue.priority] for p in priorities)):
        return "Refused: the priority must be left unset unless the user approved one."
    dues = [v.strip() for k, v in fields if _key_is(k, _DUE_KEYS) and v.strip()]
    if dues and (issue.due is None or any(not d.startswith(issue.due) for d in dues)):
        return "Refused: the due date must be left unset unless the user approved one, and then exactly it."
    return None


def _names(issue: IssueDraft) -> set[str]:
    named = [issue.team, issue.project, *issue.labels]
    return {_norm(x) for n in named if n for x in (n.id, n.name) if x}


def _key_is(key: str, words: tuple[str, ...]) -> bool:
    return bool(set(send_guard.name_tokens(key)) & set(words))


def _scalars(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every scalar in the arguments with its key path (`fields_project_key`); list items inherit their list's."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, float) and value.is_integer():  # task metadata turns ints into floats
        return [(key, str(int(value)))]
    if isinstance(value, (str, int, float)):
        return [(key, str(value))]
    if isinstance(value, Mapping):
        return [f for k, v in value.items() for f in _scalars(v, f"{key}_{k}" if key else str(k))]
    if isinstance(value, (list, tuple)):
        return [f for v in value for f in _scalars(v, key)]
    return []


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip().casefold()
