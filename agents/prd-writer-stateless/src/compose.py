"""Pure: the author's and editor's PRDs into the checked PRD. The editor's version is used when it is a usable
PRD, else the author's; then code checks it (grounding, vague tags, priorities) and adds its open questions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import gaps
import parse
from checks import check
from prd import Fact, Prd
from title import feature_title


@dataclass(frozen=True)
class Composed:
    status: str              # ok | off_topic
    prd: Prd = field(default_factory=Prd)
    dropped: int = 0
    edited: bool = False


@dataclass(frozen=True)
class Grounds:
    """What a PRD is checked against: the facts, the grounding corpus (brief, instruction, answers) and the
    audience named by the user, plus the org-policy parts for the prompts."""

    facts: tuple[Fact, ...]
    corpus: str
    audience: str = ""
    policy: tuple[str, ...] = ()


def renumber(prd: Prd) -> Prd:
    reqs = tuple(replace(r, id=f"R{n}") for n, r in enumerate(prd.requirements, start=1))
    return prd.with_(requirements=reqs, next_req=len(reqs) + 1)


def compose(author: object, editor: object, grounds: Grounds, title: str) -> Composed:
    """`author` and `editor` are the parsed JSON answers (or None); `title` is the user's, or "" for the feature's."""
    drafted, edited = parse.validate(author), parse.validate(editor)
    if drafted is None and edited is None:
        raise ValueError("neither the author nor the editor returned a PRD")
    chosen = edited if edited is not None else drafted
    if chosen["status"] == "off_topic":
        return Composed("off_topic")
    named = title or feature_title(chosen, grounds.facts, grounds.corpus)
    draft = renumber(parse.to_prd(chosen, _known(grounds), named))
    return finish(draft, grounds, edited=edited is not None)


def finish(draft: Prd, grounds: Grounds, edited: bool = False) -> Composed:
    """check -> gap questions."""
    checked = check(draft, grounds.facts, grounds.corpus)
    final = gaps.with_questions(checked.prd, grounds.facts, grounds.corpus, grounds.audience)
    return Composed("ok", final, checked.dropped, edited)


def _known(grounds: Grounds) -> frozenset[str]:
    return frozenset(f.id for f in grounds.facts)

