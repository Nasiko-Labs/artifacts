"""The only write: a nudge comment on one stale pull request. The text is a code template filled from
checked facts (days quiet, who is awaited); the model never writes it. The destination is the stale
pull request itself (repository + number from the stored queue); the comment tool is chosen in code.
Nothing is written here: `Proposal`s go to the approval walk, one card per comment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import callers
from deps import TurnDeps
from discovery import Discoverer
from queue_rules import NOBODY, TEAM_PREFIX, Assessed
from render import primary

MAX_CARDS = 5
_FINAL = frozenset({"sent", "denied"})
SENTENCE = {
    "awaiting_reviewer": "review requested, no review since the last push",
    "awaiting_first_review": "no reviewer assigned yet",
    "changes_requested": "changes were requested and nothing has been pushed since",
    "failing_checks": "checks are failing",
    "merge_conflict": "it has a merge conflict",
    "ready_to_merge": "it is approved and ready to merge",
}


@dataclass(frozen=True)
class Proposal:
    key: str            # "nudge:<owner>/<repo>#<number>": one nudge per pull request per conversation
    tool: str
    owner: str
    repo: str
    number: str
    pr_title: str
    body: str
    mention: str = ""   # the awaited login, offered only as its own option

    @property
    def destination(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Proposal:
        return Proposal(**{f.name: str(data.get(f.name) or "") for f in fields(Proposal)})


def target(a: Assessed) -> tuple[str, str]:
    """(who is asked, why): the first awaited person, else the author."""
    b = primary(a)
    author = a.facts.pr.author or "the author"
    if b is None:
        return author, "no blocker was found"
    if b.kind == "awaiting_first_review":
        return "a first reviewer", SENTENCE[b.kind]
    return b.who, SENTENCE[b.kind]


def template(a: Assessed) -> str:
    who, why = target(a)
    return (f"This PR has had no activity for {a.quiet_days} days. It is waiting on {who} ({why}). "
            "Is it still wanted? If so, can it move forward, or should it be closed?")


def mention_of(a: Assessed) -> str:
    who, _ = target(a)
    plain = who not in (NOBODY, "a first reviewer", "the author") and not who.startswith(TEAM_PREFIX)
    return who if plain and " " not in who else ""


@dataclass(frozen=True)
class Prepared:
    reply: str = ""
    proposals: tuple[Proposal, ...] = ()


async def prepare(deps: TurnDeps, chosen: Sequence[Assessed], outcomes: Mapping[str, str]) -> Prepared:
    """One proposal per chosen stale pull request not already nudged in this conversation."""
    todo = [a for a in chosen if outcomes.get(_key(a)) not in _FINAL][:MAX_CARDS]
    if not todo:
        return Prepared("I already handled a nudge for " + ", ".join(f"{a.facts.key}" for a in chosen)
                        + " in this conversation; I won't post it twice.")
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Prepared(failure)
    tools = (await discoverer.catalog("nudge")).write_tools()
    out = []
    for a in todo:
        owner, repo = a.facts.repo.split("/", 1)
        context = {"owner": owner, "repo": repo, "number": a.facts.pr.number, "body": template(a)}
        chosen_tool = callers.pick(tools, "nudge", context)
        if chosen_tool is None:
            return Prepared(copyable("I have no granted tool to comment on a pull request.", template(a)))
        out.append(Proposal(_key(a), chosen_tool[0].name, owner, repo, a.facts.pr.number, a.facts.pr.title,
                            template(a), mention_of(a)))
    return Prepared(proposals=tuple(out))


def _key(a: Assessed) -> str:
    return f"nudge:{a.facts.key}"


def copyable(reason: str, body: str) -> str:
    return f"{reason} Here is the text to copy:\n\n{body}"
