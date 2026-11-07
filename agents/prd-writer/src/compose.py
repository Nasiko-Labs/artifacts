"""Compose a PRD from checked facts (one call), check it in code, spend at most one repair call on the
problems the model can fix, check again, then add code's open questions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import gaps
import parse
import prompts
from checks import check
from facts import facts_block
from llm import LlmError, TurnDeps, complete_json
from prd import Fact, Prd

MAX_PROBLEMS_LISTED = 12


@dataclass(frozen=True)
class Composed:
    status: str              # ok | off_topic
    prd: Prd = field(default_factory=Prd)
    dropped: int = 0
    repaired: bool = False


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


def prd_json(prd: Prd) -> str:
    data = prd.to_dict()
    data["open_questions"] = [q["question"] for q in data["open_questions"] if q["section"] == "general"]
    for key in ("title", "next_req", "notes"):
        data.pop(key, None)
    for r in data["requirements"]:
        for key in ("from_solution", "vague"):
            r.pop(key, None)
    return json.dumps(data, ensure_ascii=False)


async def compose(deps: TurnDeps, grounds: Grounds, instruction: str, title: str) -> Composed:
    messages = prompts.compose_messages(facts_block(grounds.facts), instruction, grounds.policy)
    raw = await complete_json(deps, messages, prompts.PRD_CONTRACT, parse.validate)
    if raw["status"] == "off_topic":
        return Composed("off_topic")
    draft = renumber(parse.to_prd(raw, _known(grounds), title))
    return await finish(deps, draft, grounds, keep=lambda fixed: renumber(fixed))


async def finish(deps: TurnDeps, draft: Prd, grounds: Grounds, keep: Callable[[Prd], Prd]) -> Composed:
    """check -> [one repair call] -> check -> gap questions. `keep` re-applies the caller's invariants
    (ids, untouched sections) to the repaired draft."""
    checked = check(draft, grounds.facts, grounds.corpus)
    repaired = False
    if checked.problems and deps.budget.left() > 0 and deps.remaining() > 1:
        messages = prompts.fix_messages(prd_json(draft), facts_block(grounds.facts),
                                        checked.problems[:MAX_PROBLEMS_LISTED], grounds.policy)
        try:
            raw = await complete_json(deps, messages, prompts.PRD_CONTRACT, parse.validate)
        except LlmError:  # the unrepaired, checked draft is still a valid answer
            raw = {"status": "off_topic"}
        if raw["status"] == "ok":
            fixed = keep(parse.to_prd(raw, _known(grounds), draft.title)).with_(next_req=draft.next_req)
            checked, repaired = check(_keep_next(fixed, draft), grounds.facts, grounds.corpus), True
    final = gaps.with_questions(checked.prd, grounds.facts, grounds.corpus, grounds.audience)
    return Composed("ok", final, checked.dropped, repaired)


def _keep_next(fixed: Prd, draft: Prd) -> Prd:
    highest = max((int(r.id[1:]) for r in fixed.requirements if r.id[1:].isdigit()), default=0)
    return fixed.with_(next_req=max(draft.next_req, highest + 1))


def _known(grounds: Grounds) -> frozenset[str]:
    return frozenset(f.id for f in grounds.facts)

