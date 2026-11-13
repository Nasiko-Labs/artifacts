"""Pure: apply the reviser's answer to a stored PRD. Code decides which sections the request names; every other
section is restored byte-for-byte, requirement ids stay stable and a dropped id is never reused.

`revise_shown` applies it to the PRD the user last saw (read back from history): entries the user already saw
stay exactly as shown, only new or changed entries go through the content checks (unsupported claims are
stripped), and the gap questions are recomputed. `revise` is the fallback on a PRD rebuilt this turn."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import gaps
import parse
from checks import check
from compose import Composed, Grounds, finish
from grounding import normalise
from prd import SECTIONS, Metric, Prd, Requirement

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
    """What the user reads of an entry (fact ids are not shown)."""
    if isinstance(entry, Requirement):
        return "r", entry.id, _words(entry.text), entry.priority, _words(entry.acceptance_hint)
    if isinstance(entry, Metric):
        return "m", _words(entry.name), entry.baseline, entry.target, entry.window
    text = getattr(entry, "text", None) or getattr(entry, "question", None)
    return ("s", _words(text)) if isinstance(text, str) else (entry,)


def _words(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def named_sections(request: str) -> list[str]:
    """What the reviser is told it may change."""
    aim = target(request)
    named = list(aim.sections)
    return [*named, f"requirement ids {', '.join(aim.req_ids)} only"] if aim.req_ids else named


def revise(stored: Prd, raw: object, grounds: Grounds, request: str) -> tuple[Composed, bool]:
    """(the revised, checked PRD, whether code restored a section the model changed without being asked).
    Raises ValueError when the answer is not a PRD."""
    data = parse.validate(raw)
    if data is None:
        raise ValueError("the reviser did not return a PRD")
    if data["status"] == "off_topic":
        return Composed("off_topic"), False
    aim = target(request)
    revised = parse.to_prd(data, frozenset(f.id for f in grounds.facts), stored.title)
    restored = changed_elsewhere(stored, revised, aim)
    return finish(merge(stored, revised, aim), grounds), restored


def revise_shown(shown: Prd, data: dict, grounds: Grounds, request: str) -> tuple[Composed, bool]:
    """(the PRD the user saw with the reviser's change in the named sections, whether code restored a section the
    model changed without being asked). `data` is the reviser's validated answer."""
    if data["status"] == "off_topic":
        return Composed("off_topic"), False
    aim = target(request)
    revised = parse.to_prd(data, frozenset(f.id for f in grounds.facts), shown.title)
    merged = merge(shown, revised, aim)
    changed, dropped = _check_changes(shown, merged, aim, grounds)
    stale = {normalise(q.question) for q in gaps.mandatory(shown, grounds.facts, grounds.corpus, grounds.audience)}
    own = tuple(q for q in changed.open_questions if normalise(q.question) not in stale)
    final = gaps.with_questions(changed.with_(open_questions=own), grounds.facts, grounds.corpus, grounds.audience)
    return Composed("ok", final, dropped), changed_elsewhere(shown, revised, aim)


def _check_changes(shown: Prd, merged: Prd, aim: Target, grounds: Grounds) -> tuple[Prd, int]:
    """Entries already shown are kept as shown; the new or changed ones are checked against the facts."""
    names = [n for n in aim.sections if n != "open_questions"]
    seen = {n: {_plain(e): e for e in getattr(shown, n)} for n in names}
    fresh = {n: tuple(e for e in getattr(merged, n) if _plain(e) not in seen[n]) for n in names}
    checked = check(Prd(title=merged.title, **fresh), grounds.facts, grounds.corpus)
    rejoined = {n: _rejoin(getattr(merged, n), seen[n], getattr(checked.prd, n), n) for n in names}
    return merged.with_(**rejoined), checked.dropped


def _rejoin(entries: tuple, seen: dict, checked: tuple, name: str) -> tuple:
    """`entries` in order: a shown one as shown, a fresh one as the checks left it (or gone if they dropped it).
    Goals are never dropped (an invented value becomes a placeholder), so they align by place."""
    in_order = iter(checked)
    by_ident = {_ident(e): e for e in checked}
    out = []
    for entry in entries:
        kept = seen.get(_plain(entry))
        if kept is None:
            kept = next(in_order, None) if name == "goals" else by_ident.get(_ident(entry))
        if kept is not None:
            out.append(kept)
    return tuple(out)


def _ident(entry: object) -> str:
    """What the checks never change: a requirement's id, a metric's name, a statement's words."""
    if isinstance(entry, Requirement):
        return entry.id
    return entry.name if isinstance(entry, Metric) else getattr(entry, "text", "")
