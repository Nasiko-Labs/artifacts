"""Pure: code-host organization facts read out of raw tool output by shape (the field names a code-host
API returns), so code reads members, owners, outside collaborators, repository permissions and
profiles itself instead of trusting the model to copy them. Every value came from a tool result this
turn."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

MAX_REPOS = 30
_OWNER_ROLES = frozenset({"admin", "owner", "owners", "org_admin"})
_ADMIN_PERMS = ("admin", "maintain")


@dataclass(frozen=True)
class Call:
    step: str
    tool: str
    arguments: Mapping[str, Any]
    text: str


@dataclass(frozen=True)
class Profile:
    login: str
    email: str = ""
    name: str = ""


@dataclass(frozen=True)
class OrgData:
    org: str
    members: tuple[str, ...] = ()                 # lowercased logins, owners included
    owners: tuple[str, ...] = ()
    outside: tuple[str, ...] = ()                 # outside collaborators
    teams: int = 0
    repos_listed: int = 0
    repo_admins: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)  # login → ((repo, perm),)
    repos_checked: tuple[str, ...] = ()
    repos_capped: int = 0                         # repositories seen but not checked (over the cap)
    profiles: Mapping[str, Profile] = field(default_factory=dict)

    @property
    def read_anything(self) -> bool:
        return bool(self.members or self.outside or self.repos_checked)


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def objects(text: str) -> Iterator[Mapping[str, Any]]:
    yield from _walk(parse_json(text), 0)


def _walk(value: Any, depth: int) -> Iterator[Mapping[str, Any]]:
    if depth > 12:
        return
    if isinstance(value, Mapping):
        yield value
        for v in value.values():
            yield from _walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, depth + 1)
    elif isinstance(value, str) and value[:1] in "[{" and len(value) < 2_000_000:
        yield from _walk(parse_json(value), depth + 1)


def s(value: Any) -> str:
    return "" if isinstance(value, bool) or value is None else str(value)


def _login(o: Mapping[str, Any]) -> str:
    user = o.get("user")
    if isinstance(user, Mapping) and s(user.get("login")) and "role" in o:
        return s(user["login"]).casefold()
    if s(o.get("login")) and s(o.get("type")).casefold() not in ("organization", "bot"):
        return s(o["login"]).casefold()
    return ""


def is_outside_call(call: Call) -> bool:
    return "outside" in call.tool.casefold() or s(call.arguments.get("filter")).casefold() == "outside"


def org_members(calls: Sequence[Call]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], int]:
    """(members, owners, outside collaborators, team count) from the member-step calls."""
    members, owners, outside, teams = [], [], [], set()
    for call in calls:
        admin_filter = s(call.arguments.get("role")).casefold() in _OWNER_ROLES
        for o in objects(call.text):
            if s(o.get("slug")) and "login" not in o:
                teams.add(s(o["slug"]).casefold())
                continue
            login = _login(o)
            if not login:
                continue
            if is_outside_call(call):
                outside.append(login)
                continue
            members.append(login)
            if admin_filter or s(o.get("role")).casefold() in _OWNER_ROLES:
                owners.append(login)
    outside_u = _uniq(outside)
    return _uniq(m for m in members if m not in outside_u), _uniq(owners), outside_u, len(teams)


def _uniq(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def repo_permissions(calls: Sequence[Call]) -> tuple[dict[str, tuple[tuple[str, str], ...]], tuple[str, ...], int, int]:
    """(login → ((repo, permission),) for admin/maintain, repositories checked, repositories listed, capped)."""
    listed: list[str] = []
    checked: list[str] = []
    admins: dict[str, tuple[tuple[str, str], ...]] = {}
    for call in calls:
        for o in objects(call.text):
            name = s(o.get("full_name"))
            if "/" in name and ("private" in o or "default_branch" in o or "visibility" in o):
                listed.append(name.casefold())
        repo = repo_of(call.arguments)
        rows = [(o, _login(o)) for o in objects(call.text) if ("permissions" in o or "role_name" in o)]
        rows = [(o, login) for o, login in rows if login]
        if not repo or not rows:
            continue
        if repo not in checked:
            if len(checked) >= MAX_REPOS:
                continue
            checked.append(repo)
        for o, login in rows:
            perm = _permission(o)
            if perm in _ADMIN_PERMS:
                prior = admins.get(login, ())
                admins = {**admins, login: tuple(dict.fromkeys((*prior, (repo, perm))))}
    seen = set(dict.fromkeys(listed)) | set(checked)
    return admins, tuple(checked), len(set(listed)), max(0, len(seen) - len(checked))


def _permission(o: Mapping[str, Any]) -> str:
    role = s(o.get("role_name")).casefold()
    if role:
        return role
    perms = o.get("permissions")
    if isinstance(perms, Mapping):
        return next((p for p in ("admin", "maintain", "push", "triage", "pull") if perms.get(p) is True), "")
    return ""


def profiles(calls: Sequence[Call]) -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    for call in calls:
        for o in objects(call.text):
            login = s(o.get("login")).casefold()
            if login and ("email" in o or "name" in o) and s(o.get("type")).casefold() != "organization":
                out.setdefault(login, Profile(login, s(o.get("email")).strip().casefold(), s(o.get("name")).strip()))
    return out


def repo_of(arguments: Mapping[str, Any]) -> str:
    lowered = {str(k).casefold(): v for k, v in arguments.items()}
    for key in ("full_name", "repository", "repo"):
        value = s(lowered.get(key))
        if "/" in value:
            return value.strip("/").casefold()
    owner = next((s(lowered[k]) for k in ("owner", "org", "organization") if s(lowered.get(k))), "")
    repo = next((s(lowered[k]) for k in ("repo", "repository", "repo_name", "name") if s(lowered.get(k))), "")
    return f"{owner}/{repo}".casefold() if owner and repo else ""


def repo_names(text: str) -> list[str]:
    """Repository names in a listing, for the model's view of it (names only, nothing personal)."""
    return list(dict.fromkeys(s(o["full_name"]) for o in objects(text)
                              if "/" in s(o.get("full_name")) and ("private" in o or "default_branch" in o
                                                                   or "visibility" in o)))
