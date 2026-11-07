"""Pure: from the gathered evidence (what the read steps returned, stored in the conversation) to the
answer: linked people, ranked by score, with `confidence` and `needs_human` computed here from checks,
never taken from the model.

confidence: high when the top person's evidence spans >= 2 sources, every source was read and nothing
was dropped; medium when a source was not read, an item was dropped or the evidence is from one source;
low when only one source was read, a step was salvaged, a shown person's evidence is weak, or the
evidence was pasted. needs_human: an ambiguous identity or weak evidence among the people shown, no
clear owner, or "exclude me" asked but the user could not be identified."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date

import identity
from models import SOURCES, Evidence, Who
from score import Scored, rank

SHOWN = 2
MAX_REPO_OPTIONS = 8
ALL_REPOS = "*"  # the repo question was answered with text naming no repository: keep them all


@dataclass(frozen=True)
class Gathered:
    """Everything a turn read, as stored in the conversation (so follow-ups need no tool call)."""

    topic: str
    terms: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    statuses: Mapping[str, tuple[str, str]] = field(default_factory=dict)  # source → (state, reason)
    as_of: date = date(1970, 1, 1)
    dropped: int = 0
    salvaged: bool = False
    exclude_me: bool = False
    me: Who | None = None
    mode: str = "live"  # live | pasted
    repo: str = ""

    def to_dict(self) -> dict:
        return {"topic": self.topic, "terms": list(self.terms), "evidence": [e.to_dict() for e in self.evidence],
                "statuses": {k: list(v) for k, v in self.statuses.items()}, "as_of": self.as_of.isoformat(),
                "dropped": self.dropped, "salvaged": self.salvaged, "exclude_me": self.exclude_me,
                "me": None if self.me is None else {"name": self.me.name, "email": self.me.email,
                                                    "handle": self.me.handle},
                "mode": self.mode, "repo": self.repo}

    @staticmethod
    def from_dict(data: Mapping) -> Gathered:
        me = data.get("me")
        return Gathered(topic=str(data.get("topic") or ""), terms=tuple(data.get("terms") or ()),
                        evidence=tuple(Evidence.from_dict(e) for e in data.get("evidence") or ()),
                        statuses={k: (str(v[0]), str(v[1])) for k, v in (data.get("statuses") or {}).items()},
                        as_of=date.fromisoformat(str(data.get("as_of") or "1970-01-01")),
                        dropped=int(data.get("dropped") or 0), salvaged=bool(data.get("salvaged")),
                        exclude_me=bool(data.get("exclude_me")),
                        me=Who(str(me.get("name") or ""), str(me.get("email") or ""), str(me.get("handle") or ""))
                        if isinstance(me, Mapping) else None,
                        mode=str(data.get("mode") or "live"), repo=str(data.get("repo") or ""))

    def read_sources(self) -> tuple[str, ...]:
        return tuple(s for s in SOURCES if self.statuses.get(s, ("", ""))[0] == "ok")


@dataclass(frozen=True)
class Finding:
    gathered: Gathered
    ranked: tuple[Scored, ...]
    confidence: str
    needs_human: bool
    me_unknown: bool = False

    @property
    def shown(self) -> tuple[Scored, ...]:
        return self.ranked[:SHOWN]

    @property
    def clear_owner(self) -> bool:
        return bool(self.shown) and not self.shown[0].weak


def build(gathered: Gathered) -> Finding:
    evidence = _in_repo(gathered.evidence, gathered.repo)
    people = identity.link(evidence)
    me_unknown = gathered.exclude_me and gathered.me is None
    if gathered.exclude_me:
        people = identity.without(people, gathered.me)
    ranked = rank(people, gathered.as_of)
    shown = ranked[:SHOWN]
    confidence = _confidence(gathered, shown)
    needs_human = (not shown or shown[0].weak or any(s.weak or s.person.ambiguous for s in shown) or me_unknown)
    return Finding(gathered, ranked, confidence, needs_human, me_unknown)


def _confidence(gathered: Gathered, shown: Sequence[Scored]) -> str:
    read = gathered.read_sources()
    if (not shown or gathered.mode == "pasted" or gathered.salvaged or len(read) <= 1
            or any(s.weak for s in shown)):
        return "low"
    if len(read) < len(SOURCES) or gathered.dropped or shown[0].breadth < 2:
        return "medium"
    return "high"


def _in_repo(evidence: Sequence[Evidence], repo: str) -> tuple[Evidence, ...]:
    """With a chosen repository, code evidence from other repositories is left out (chat and docs stay)."""
    if not repo or repo == ALL_REPOS:
        return tuple(evidence)
    wanted = repo.casefold()
    return tuple(e for e in evidence if e.source != "code" or wanted in e.repo.casefold())


def repo_options(gathered: Gathered) -> tuple[str, ...]:
    """The top 8 repositories by evidence count, when more than 8 matched and none was chosen."""
    if gathered.repo:
        return ()
    counts = Counter(e.repo for e in gathered.evidence if e.source == "code" and e.repo)
    if len(counts) <= MAX_REPO_OPTIONS:
        return ()
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    return tuple(name for name, _ in ordered[:MAX_REPO_OPTIONS])


def choose_repo(gathered: Gathered, answer: str) -> Gathered:
    """The repo picked on the question (its label, or typed text naming one); unknown text keeps them all."""
    wanted = answer.strip().strip("`").casefold()
    names = {e.repo for e in gathered.evidence if e.source == "code" and e.repo}
    match = next((n for n in sorted(names) if n.casefold() == wanted), None) or next(
        (n for n in sorted(names) if wanted and wanted in n.casefold()), None)
    return replace(gathered, repo=match or ALL_REPOS)
