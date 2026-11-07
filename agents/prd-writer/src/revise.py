"""Revise the stored PRD by instruction (one call, plus at most one repair). Code decides which sections the
request names; every other section is restored byte-for-byte, requirement ids stay stable and a dropped id
is never reused."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import parse
import prompts
from compose import Composed, Grounds, finish, prd_json
from facts import facts_block
from llm import TurnDeps, complete_json
from prd import SECTIONS, Prd, Requirement

_SECTION_WORDS = (
    ("problem", r"problem|pain|why"),
    ("users", r"users?|audiences?|personas?|customers?|who it'?s for|segments?"),
    ("goals", r"(?<!non-)(?<!non )goals?|outcomes?|objectives?"),
    ("non_goals", r"non[- ]?goals?|out of scope|exclusions?"),
    ("requirements", r"requirements?|reqs?|\br\d+\b|features?|behaviou?rs?|acceptance|priorit(?:y|ies)"),
    ("metrics", r"metrics?|kpis?|success|measures?|targets?|baselines?"),
    ("open_questions", r"open questions?|questions?"),
)
_REQ_ID = re.compile(r"\b(?:r|req(?:uirement)?\s*#?\s*)(\d{1,2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    sections: tuple[str, ...]
    req_ids: tuple[str, ...] = ()


def target(request: str) -> Target:
    """The sections (and requirement ids) the request names; none named means every section."""
    text = request or ""
    sections = tuple(name for name, words in _SECTION_WORDS if re.search(rf"\b(?:{words})", text, re.IGNORECASE))
    ids = tuple(dict.fromkeys(f"R{int(n)}" for n in _REQ_ID.findall(text)))
    if ids and "requirements" not in sections:
        sections = (*sections, "requirements")
    return Target(sections or SECTIONS, ids)


def merge(stored: Prd, revised: Prd, aim: Target) -> Prd:
    """Untouched sections come back from `stored`; requirement ids are kept or freshly assigned."""
    changes = {name: getattr(revised, name) for name in SECTIONS if name in aim.sections}
    merged = stored.with_(**changes)
    if "requirements" in aim.sections:
        reqs, next_req = _merge_requirements(stored, revised.requirements, aim.req_ids)
        merged = merged.with_(requirements=reqs, next_req=next_req)
    return merged


def _merge_requirements(stored: Prd, revised: tuple[Requirement, ...],
                        only: tuple[str, ...]) -> tuple[tuple[Requirement, ...], int]:
    known = {r.id for r in stored.requirements}
    next_req = stored.next_req
    fresh: list[Requirement] = []
    by_id: dict[str, Requirement] = {}
    for r in revised:
        if r.id in known and r.id not in by_id:
            by_id[r.id] = r
        else:
            fresh.append(replace(r, id=f"R{next_req}"))
            next_req += 1
    if only:  # only the named requirements may change; the others stay exactly as stored
        kept = tuple(by_id.get(r.id, r) if r.id in only else r for r in stored.requirements)
    else:
        kept = tuple(by_id[r.id] for r in stored.requirements if r.id in by_id)
    return (*kept, *fresh), next_req


def changed_elsewhere(stored: Prd, revised: Prd, aim: Target) -> bool:
    """Did the model touch a section it was not asked to change?"""
    for name in SECTIONS:
        if name in aim.sections or name == "open_questions":
            continue
        if [_plain(x) for x in getattr(stored, name)] != [_plain(x) for x in getattr(revised, name)]:
            return True
    return False


def _plain(entry: object) -> tuple:
    if isinstance(entry, Requirement):
        return entry.id, entry.text, entry.priority, entry.basis, entry.acceptance_hint
    return tuple(getattr(entry, "__dict__", {}).values()) or (entry,)


async def revise(deps: TurnDeps, stored: Prd, grounds: Grounds, request: str) -> tuple[Composed, bool]:
    """(the revised, checked PRD, whether code restored a section the model changed without being asked)."""
    aim = target(request)
    named = [s for s in aim.sections]
    if aim.req_ids:
        named = [*named, f"requirement ids {', '.join(aim.req_ids)} only"]
    messages = prompts.revise_messages(prd_json(stored), facts_block(grounds.facts), request, named, grounds.policy)
    raw = await complete_json(deps, messages, prompts.PRD_CONTRACT, parse.validate)
    if raw["status"] == "off_topic":
        return Composed("off_topic"), False
    revised = parse.to_prd(raw, frozenset(f.id for f in grounds.facts), stored.title)
    restored = changed_elsewhere(stored, revised, aim)
    draft = merge(stored, revised, aim)
    result = await finish(deps, draft, grounds, keep=lambda fixed: merge(stored, fixed, aim))
    return result, restored
