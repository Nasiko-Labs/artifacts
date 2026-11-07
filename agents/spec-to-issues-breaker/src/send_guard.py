"""Code-enforced gate on every tracker write (adapted from the kit's send guard).

The create card shows the exact project or issue; the model then maps it onto the tool's arguments. A write
runs only when it is exactly what the user approved on that ONE card:
- the same tool (the project-create or the issue-create tool the card named);
- the title (or project name) and the description equal the approved ones (case and whitespace ignored);
- the team is the approved team (by its checked id, key or name); a project is the approved project;
- an estimate only when one was approved, and then equal to it;
- no assignee, label, priority, parent, cycle, due date or subscriber field, and no other free text.
One approval allows one write (enforced by sender.GuardedGateway). Pure: decisions only, no I/O."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "merge", "solve", "new",
})
DESTRUCTIVE_VERBS = frozenset({"delete", "remove", "trash", "merge", "archive", "destroy", "import", "restore",
                               "bulk", "permanently", "batch"})
_TITLE_KEYS = frozenset({"title", "name", "summary"})
_DESCRIPTION_KEYS = frozenset({"description", "body", "content", "text", "markdown"})
_TEAM_KEYS = frozenset({"team", "teams"})
_PROJECT_KEYS = frozenset({"project"})
_ESTIMATE_KEYS = frozenset({"estimate", "points"})
_FORBIDDEN_KEYS = frozenset({"assignee", "assign", "owner", "label", "labels", "priority", "parent", "cycle", "due",
                             "subscriber", "subscribers", "lead", "members", "milestone"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Approval:
    """What the user approved on one card. `kind` is "issue" or "project"; ids come from checked records."""

    kind: str
    tool: str
    local_id: int
    title: str
    description: str
    team_id: str
    team_key: str = ""
    team_name: str = ""
    project_id: str = ""
    project_name: str = ""
    estimate: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: object) -> Approval | None:
        if not isinstance(data, Mapping) or not data.get("tool") or not data.get("title"):
            return None
        estimate = data.get("estimate")
        return Approval(kind=str(data.get("kind") or "issue"), tool=str(data["tool"]),
                        local_id=int(data.get("local_id") or 0), title=str(data["title"]),
                        description=str(data.get("description") or ""), team_id=str(data.get("team_id") or ""),
                        team_key=str(data.get("team_key") or ""), team_name=str(data.get("team_name") or ""),
                        project_id=str(data.get("project_id") or ""), project_name=str(data.get("project_name") or ""),
                        estimate=None if estimate is None else int(estimate))


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


def is_destructive_tool(tool_name: str) -> bool:
    return any(token in DESTRUCTIVE_VERBS for token in name_tokens(tool_name))


def check(name: str, arguments: Mapping[str, Any], approval: Approval | None) -> str | None:
    """None when the call is exactly the approved write; else the reason it is refused."""
    if approval is None:
        return "Refused: creating needs the user's approval on its own card first. Nothing was created."
    if name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved a write with {approval.tool}, not {name}."
    fields = _scalars(arguments)
    return (_forbidden_problem(fields) or _text_problem(fields, approval) or _team_problem(fields, approval)
            or _estimate_problem(fields, approval))


def _forbidden_problem(fields: list[tuple[str, str]]) -> str | None:
    bad = sorted({k for k, v in fields if _key_in(k, _FORBIDDEN_KEYS) and v.strip()})
    return f"Refused: {', '.join(bad)} may not be set; the user approved none." if bad else None


def _text_problem(fields: list[tuple[str, str]], approval: Approval) -> str | None:
    title, body = _norm(approval.title), _norm(approval.description)
    titles = [_norm(v) for k, v in fields if _key_in(k, _TITLE_KEYS)]
    bodies = [_norm(v) for k, v in fields if _key_in(k, _DESCRIPTION_KEYS)]
    if not titles or any(t != title for t in titles):
        return "Refused: the title must be exactly the approved title, verbatim."
    if body and (not bodies or any(b != body for b in bodies)):
        return "Refused: the description must be exactly the approved description, verbatim."
    if not body and bodies:
        return "Refused: no description was approved."
    allowed = {title, body, *_names(approval)}
    stray = sorted({k or "?" for k, v in fields if " " in v.strip() and _norm(v) not in allowed})
    return f"Refused: this call carries text the user did not approve (in {', '.join(stray)})." if stray else None


def _team_problem(fields: list[tuple[str, str]], approval: Approval) -> str | None:
    team_ids = {_norm(x) for x in (approval.team_id, approval.team_key, approval.team_name) if x}
    project_ids = {_norm(x) for x in (approval.project_id, approval.project_name) if x}
    teams = [_norm(v) for k, v in fields if _key_in(k, _TEAM_KEYS) and v.strip()]
    projects = [_norm(v) for k, v in fields if _key_in(k, _PROJECT_KEYS) and not _key_in(k, _TITLE_KEYS) and v.strip()]
    if team_ids and not teams:
        return "Refused: the approved team is missing from the call."
    if any(t not in team_ids for t in teams):
        return "Refused: the team in this call is not the approved team."
    if approval.kind == "issue" and any(p not in project_ids for p in projects):
        return "Refused: the project in this call is not the approved project."
    return None


def _estimate_problem(fields: list[tuple[str, str]], approval: Approval) -> str | None:
    estimates = [v.strip() for k, v in fields if _key_in(k, _ESTIMATE_KEYS) and v.strip()]
    if estimates and (approval.estimate is None or any(e != str(approval.estimate) for e in estimates)):
        return "Refused: the estimate must be left unset unless the user approved one, and then exactly it."
    return None


def _names(approval: Approval) -> set[str]:
    return {_norm(x) for x in (approval.team_name, approval.project_name, approval.team_key) if x}


def _key_in(key: str, words: frozenset[str]) -> bool:
    return bool(set(name_tokens(key)) & words)


def _scalars(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every scalar in the arguments with its key path (`input_team_id`); list items inherit their list's."""
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
