"""One review run over already-scanned evidence:

    tag (<= 5 calls, untagged items only) -> check (code) -> draft (1) -> verify (code) -> [repair (1)] -> bias

At most 7 calls. When the turn deadline is too near to draft, or the draft call fails, code returns the
evidence table by competency instead of prose (confidence low)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import bias
import coverage
import scan
from dates import parse_period
from draft import Request, draft
from envelope import Review
from evidence import Evidence
from lexicons import allowed_pronouns
from llm import OPTIONAL_CALL_RESERVE_SECS, TurnDeps
from tag import tag
from verify import Context

DEFAULT_PERSON = "this person"


@dataclass(frozen=True)
class Outcome:
    status: str                 # ok | off_topic | failed
    review: Review | None = None


def person_of(header: dict) -> str:
    return (header.get("person") or "").strip()[:60] or DEFAULT_PERSON


def _period(header: dict, items: Sequence[Evidence]):
    stated = parse_period(header.get("period") or "")
    return stated, bias.period_of(stated, items)


async def run(deps: TurnDeps, items: Sequence[Evidence], header: dict, policy: Sequence[str] = (),
              notes: Sequence[str] = (), left_out: Sequence[dict] = ()) -> Outcome:
    names = coverage.competencies(header)
    person = person_of(header)
    tagged = await tag(deps, items, names, person, policy)
    if tagged.off_topic:
        return Outcome("off_topic")
    if tagged.batches and tagged.failed_batches == tagged.batches:
        return Outcome("failed")
    evidence = tagged.items
    notes = list(notes)
    if tagged.failed_batches:
        notes.append(f"_{tagged.failed_batches} of {tagged.batches} tagging batches failed; those items were not "
                     "used. Ask again to retry._")
    return await compose(deps, evidence, header, policy, tuple(notes), tuple(left_out))


async def compose(deps: TurnDeps, evidence: Sequence[Evidence], header: dict, policy: Sequence[str],
                  notes: tuple[str, ...], left_out: tuple[dict, ...]) -> Outcome:
    """Draft and check over tagged evidence (also the "remove e5" path: no tag call)."""
    names = coverage.competencies(header)
    person = person_of(header)
    usable = coverage.usable(evidence)
    gaps = coverage.gaps(evidence, header)
    stated, period = _period(header, usable)
    base = Review(person=person, role=(header.get("role") or "")[:80], period=(header.get("period") or "")[:60],
                  evidence=tuple(evidence), gaps=tuple(gaps), left_out=left_out, notes=notes)
    visible = [e for e in evidence if not e.excluded]
    extra = _notes(evidence, stated is not None)
    if not usable or not deps.has_time(OPTIONAL_CALL_RESERVE_SECS):
        flags = bias.checks(usable, visible, (), period)[:-1] + extra
        return Outcome("ok", replace(base, flags=tuple(flags), fallback=True))
    ctx = Context(usable={e.id: e for e in usable}, names=list(names), gaps=[g["competency"] for g in gaps],
                  person=person, pronouns=frozenset(allowed_pronouns(header.get("pronouns") or "")))
    rows = coverage.table(evidence, list(names))
    request = Request(tuple(usable), rows, ctx, names, person, header.get("pronouns") or "",
                      "short" if (header.get("length") or "").casefold().startswith("short") else "standard")
    drafted = await draft(deps, request, policy)
    if drafted.status == "off_topic":
        return Outcome("off_topic")
    if drafted.status == "failed":
        flags = bias.checks(usable, visible, (), period)[:-1] + extra
        return Outcome("ok", replace(base, flags=tuple(flags), fallback=True))
    flags = bias.checks(usable, visible, drafted.statements, period) + extra
    return Outcome("ok", replace(base, statements=drafted.statements, flags=tuple(flags), dropped=drafted.dropped))


def _notes(evidence: Sequence[Evidence], has_period: bool) -> list[dict]:
    extra: list[dict] = []
    if has_period and scan.mentions_leave(evidence):
        extra.append(bias.flag("leave", scan.LEAVE_NOTE, is_note=True))
    general = coverage.unusable_items(evidence)
    if general:
        listed = "; ".join(f"{e.id} \"{e.text[:50]}\"" for e in general)
        extra.append(bias.flag("unusable", f"Too general to use as evidence: {listed}.", is_note=True))
    return extra
