"""The duplicate check: one tracker search per proposed title inside one bounded loop; every existing issue the
model reports must appear in the search output. Matching is code: equal titles (casefold, trimmed) or at least
80% shared title words. A matched issue is marked `exists` and defaults to skipped. A failed search marks
nothing and adds a note."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import prompts
import replies
from deps import TurnDeps
from discovery import discover
from llmcall import load_json_object, text_of
from reader import TurnReader
from records import ProposedIssue, Team
from textcheck import appears_in

DUP_LOOP_CALLS = 5
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Existing:
    title: str
    identifier: str
    url: str = ""


@dataclass(frozen=True)
class DupResult:
    issues: tuple[ProposedIssue, ...]
    ok: bool
    notes: tuple[str, ...] = ()
    salvaged: bool = False


def parse_existing(text: str) -> tuple[Existing, ...] | None:
    data = load_json_object(text)
    if data is None or not isinstance(data.get("existing"), list):
        return None
    out = []
    for raw in data["existing"]:
        if not isinstance(raw, Mapping):
            continue
        ident = text_of(raw.get("identifier")) or text_of(raw.get("id"))
        out.append(Existing(title=text_of(raw.get("title")), identifier=ident, url=text_of(raw.get("url"))))
    return tuple(out)


def overlap(a: str, b: str) -> float:
    """Shared words over the larger word set: 1.0 for the same words, 0 for none."""
    left, right = set(_WORD.findall(a.casefold())), set(_WORD.findall(b.casefold()))
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left), len(right))


def is_same(proposed: str, existing: str, threshold: float) -> bool:
    return proposed.strip().casefold() == existing.strip().casefold() or overlap(proposed, existing) >= threshold


def mark(issues: Sequence[ProposedIssue], existing: Sequence[Existing], threshold: float) -> tuple[ProposedIssue, ...]:
    marked = []
    for issue in issues:
        hit = next((e for e in existing if is_same(issue.title, e.title, threshold)), None)
        marked.append(replace(issue, exists=hit.identifier, exists_url=hit.url, status="skipped") if hit else issue)
    return tuple(marked)


async def dup_check(deps: TurnDeps, reader: TurnReader, team: Team,
                    issues: Sequence[ProposedIssue]) -> DupResult:
    if not issues:
        return DupResult(tuple(issues), True)
    catalog, failure = await discover(deps, "dup")
    tools = catalog.read_tools("tracker") if catalog else ()
    if failure is not None or not tools:
        return DupResult(tuple(issues), False, (replies.DUP_CHECK_FAILED_NOTE,))
    before = len(reader.calls)
    titles = "\n".join(f"{i.local_id}. {i.title}" for i in issues)
    run = await reader.run_json(deps, tools, prompts.dup_system(team.name), prompts.fence_list(titles),
                                DUP_LOOP_CALLS, parse_existing)
    searched = [c for c in reader.calls[before:] if not c.is_error]
    if run.parsed is None or not searched:
        return DupResult(tuple(issues), False, (*run.notes, replies.DUP_CHECK_FAILED_NOTE))
    seen = "\n".join(c.body for c in searched)
    verified = tuple(e for e in run.parsed if e.identifier and e.title and appears_in(e.identifier, seen)
                     and appears_in(e.title, seen))
    verified = tuple(e if appears_in(e.url, seen) else replace(e, url="") for e in verified)
    return DupResult(mark(issues, verified, deps.config.duplicate_overlap), True, run.notes, run.salvaged)
