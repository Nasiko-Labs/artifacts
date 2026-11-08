"""Pure: follow-ups on the last result, read in code (sentinels), answered from state with no model or tool
call, except "search <owner/repo>" which runs the identifier searches in the new repositories.

    only destroys / replacements   → the result filtered to destroys and replacements
    ignore tag-only changes        → the result without tag-only changes
    who owns <module or address>   → the owners of its dependents (CODEOWNERS or commits; else "unknown")
    why is <address> high          → its score parts, from the risk table
    search <owner/repo> too        → (pipeline.search_more)
    as json                        → the envelope
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import intake
import render
from analysis import Analysis

_DESTROYS = re.compile(r"\bonly\b.*\b(?:destroy\w*|replace\w*)\b|\bdestroys? and replace", re.IGNORECASE)
_TAGS = re.compile(r"\b(?:ignore|skip|hide|without|drop|exclude)\b.*\btag", re.IGNORECASE)
_OWNERS = re.compile(r"\bwho owns\b\s*(?P<t>[^\s?!,]+)?|\bowners? (?:of|for)\s+(?P<u>[^\s?!,]+)", re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b.*?(?P<t>module\.[\w\-\[\]\".]+|[a-z][a-z0-9]*_[\w-]+\.[\w\-\[\]\".]+)", re.IGNORECASE)
_SEARCH = re.compile(r"\b(?:search|check|look (?:in|at)|also)\b", re.IGNORECASE)
_VAGUE = {"what", "this", "the", "it", "everything", "all", "these", "them", "that"}
DESTRUCTIVE = ("delete", "replace")
LEVEL_RULE = "Levels: 7+ critical, 5-6 high, 3-4 medium, else low."


@dataclass(frozen=True)
class Ask:
    kind: str                 # destroys | no_tags | owners | why | search | json | other
    target: str = ""
    repos: tuple[str, ...] = ()


def classify(text: str) -> Ask:
    repos = intake.repos_in(text)
    if repos and _SEARCH.search(text):
        return Ask("search", repos=repos)
    owners = _OWNERS.search(text)
    if owners:
        target = (owners.group("t") or owners.group("u") or "").strip(".:;`'\"")
        return Ask("owners", "" if target.casefold() in _VAGUE else target)
    why = _WHY.search(text)
    if why:
        return Ask("why", why.group("t").strip(".:;`'\""))
    if _DESTROYS.search(text):
        return Ask("destroys")
    if _TAGS.search(text):
        return Ask("no_tags")
    if intake.wants_json(text):
        return Ask("json")
    return Ask("other")


def destroys_only(a: Analysis) -> Analysis:
    keep = {c.address for c in a.changes if c.action in DESTRUCTIVE}
    return _only(a, keep, "destroys and replacements only")


def without_tags(a: Analysis) -> Analysis:
    keep = {c.address for c in a.changes if not c.tag_only}
    return _only(a, keep, "the result without tag-only changes")


def _only(a: Analysis, keep: set[str], view: str) -> Analysis:
    return replace(a, changes=tuple(c for c in a.changes if c.address in keep),
                   affected=tuple(r for r in a.affected if r.change in keep),
                   breaks=tuple(b for b in a.breaks if b[0] in keep), narrative="", view=view)


def _matches(target: str, address: str) -> bool:
    return address == target or address.startswith((target + ".", target + "["))


def owners(a: Analysis, target: str) -> str:
    if target and not any(_matches(target, c.address) for c in a.changes):
        return f"No change in the last plan matches {render.code(target)}."
    rows = [r for r in a.affected if not target or _matches(target, r.change)]
    what = render.code(target) if target else "what this plan changes"
    if not rows:
        return (f"No dependents of {what} were found in the code host, so I have no owners to show "
                "(I never guess owners).")
    lines = [f"- {render.plain(r.owner)}: {render.code(f'{r.path}:{r.line}' if r.line else r.path)} "
             f"({r.kind}, {r.environment}, depends on {render.code(r.change)})" for r in rows]
    return (f"**Owners of {what}** (from CODEOWNERS, else the top committer of the last 90 days; \"unknown\" when "
            "neither exists)\n" + "\n".join(lines))


def why(a: Analysis, target: str) -> str:
    row = next((c for c in a.changes if c.address == target), None) or next(
        (c for c in a.changes if _matches(target, c.address)), None)
    if row is None:
        return f"No change in the last plan matches {render.code(target)}."
    deps = [r for r in a.affected if r.change == row.address]
    prod = sum(1 for r in deps if r.environment == "prod")
    parts = " + ".join(row.parts) or "no risk points"
    return (f"{render.code(row.address)} is **{row.risk_level.upper()}** (score {row.score}): {parts}. "
            f"{LEVEL_RULE} It has {len(deps)} dependent(s) found, {prod} in prod.")
