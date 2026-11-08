"""Pure: which changed resources are searched for dependents, and the exact searches for each.

Selection: changes that touch something existing (destroy, replace, non-tag update), top 12 by risk
score (then address). Per resource, at most 3 searches:
    1. definition       `resource "<type>" "<name>"`                          in the IaC repository
    2. module_call      `module "<outermost module>"` (module-addressed only)  in the IaC repository
       or ref           `<type>.<name>` (top-level resources)                  in the IaC repository
    3. identifier       the resource's distinctive value from the plan (host, bucket, identifier, name)
                        in the IaC repository and every other repository the user named
Plus one shared `terraform_remote_state` search in the IaC repository (state consumers).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import tf_types
from plan_model import SENSITIVE, UNKNOWN, Change, bare_name
from risk import Risk

MAX_RESOURCES = 12
MAX_SEARCHES_PER_RESOURCE = 3
MAX_EXTRA_REPOS = 5
MIN_IDENTIFIER_CHARS = 5
LONG_IDENTIFIER_CHARS = 8
REMOTE_STATE = "terraform_remote_state"
_PORT = re.compile(r":\d{2,5}$")
_SEARCHED_ACTIONS = ("delete", "replace", "update")


@dataclass(frozen=True)
class Needle:
    kind: str        # definition | module_call | ref | identifier | remote_state
    text: str
    repo: str
    address: str     # the change it belongs to ("" for the shared remote-state search)


def select(changes: Sequence[Change], risks: Mapping[str, Risk]) -> tuple[tuple[Change, ...], int]:
    """(the resources to search, how many were eligible)."""
    eligible = [c for c in changes if c.action in _SEARCHED_ACTIONS and not c.tag_only and c.mode == "managed"]
    ranked = sorted(eligible, key=lambda c: (-risks[c.address].score, c.address))
    return tuple(ranked[:MAX_RESOURCES]), len(eligible)


def identifier(change: Change) -> str:
    """The resource's distinctive value from the plan, or "" when none is safe and specific enough."""
    known = dict(change.known)
    for key in tf_types.IDENTIFIER_KEYS:
        value = _PORT.sub("", (known.get(key) or "").strip())
        if not value or value in (UNKNOWN, SENSITIVE) or tf_types.is_sensitive_key(key) or value.isdigit():
            continue
        specific = len(value) >= LONG_IDENTIFIER_CHARS or any(ch in value for ch in "-._")
        if len(value) >= MIN_IDENTIFIER_CHARS and specific and " " not in value:
            return value
    return ""


def needles_for(change: Change, repo: str, extra_repos: Sequence[str]) -> list[Needle]:
    name = bare_name(change.name)
    out = [Needle("definition", f'resource "{change.rtype}" "{name}"', repo, change.address)]
    if change.module_name:
        out.append(Needle("module_call", f'module "{change.module_name}"', repo, change.address))
    else:
        out.append(Needle("ref", f"{change.rtype}.{name}", repo, change.address))
    ident = identifier(change)
    if ident:
        out += [Needle("identifier", ident, r, change.address) for r in (repo, *extra_repos[:MAX_EXTRA_REPOS])]
    return out


def plan(selected: Sequence[Change], repo: str, extra_repos: Sequence[str]) -> tuple[Needle, ...]:
    if not selected:
        return ()
    needles = [n for c in selected for n in needles_for(c, repo, extra_repos)]
    if repo:
        needles.append(Needle("remote_state", REMOTE_STATE, repo, ""))
    return tuple(n for n in needles if n.repo)


def identifier_only(selected: Sequence[Change], repos: Sequence[str]) -> tuple[Needle, ...]:
    """The follow-up "search <repo>": only the identifiers, only in the new repositories."""
    return tuple(Needle("identifier", ident, r, c.address) for c in selected if (ident := identifier(c))
                 for r in repos)


def describe(needles: Sequence[Needle]) -> str:
    """The numbered search list the search step's prompt carries (texts verbatim)."""
    unique = list(dict.fromkeys((n.repo, n.text) for n in needles))
    return "\n".join(f"{i}. In {repo}: {text}" for i, (repo, text) in enumerate(unique, start=1))
