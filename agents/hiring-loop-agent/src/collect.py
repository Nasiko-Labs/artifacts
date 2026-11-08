"""Collect feedback from the user's email and shared documents (read-only), when nothing was pasted.

Each source is one bounded tool loop. What the model transcribes is kept only when the candidate, the
interviewer and the feedback sentences appear verbatim in that step's tool output. Several candidate names
that share a word with the requested one ("Jordan Lee", "Jordan Li") make the turn pause on one question.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import coverage
import prompts
import request
from checks import items, text_of
from deps import TurnDeps
from discovery import Catalog, discover
from ratings import is_rating
from records import Block
from steps import StepResult, run_step

log = logging.getLogger("hiring_loop.collect")

MAX_ITEMS = 15            # messages per run, and documents per run
MAX_OPTIONS = 10
_LABEL = {"mail": "email", "drive": "document"}


@dataclass(frozen=True)
class Collected:
    blocks: tuple[Block, ...] = ()
    notes: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()       # (label, exact candidate name) when a pause is needed
    sources_read: tuple[str, ...] = ()
    any_source_failed: bool = False


async def collect(deps: TurnDeps, req: request.Request, candidate: str, chosen: bool) -> Collected:
    catalog, failure = await discover(deps)
    if catalog is None:
        return Collected(notes=(failure or prompts.GATEWAY_UNREACHABLE,), any_source_failed=True)
    asked = _asked(req)
    since, until = request.window(deps.today, req.window_days)
    systems = {
        "mail": prompts.mail_system(candidate, request.search_terms(candidate), since, until, MAX_ITEMS),
        "drive": prompts.drive_system(candidate, req.folder, MAX_ITEMS),
    }
    query = f"Candidate: {candidate}"
    by_source: dict[str, StepResult] = {}
    for source in asked:  # one after the other: email first, then documents
        by_source = {**by_source, source: await run_step(deps, source, catalog.tools_for(source), catalog.writes,
                                                         systems[source], query)}
    cover = _coverage(catalog, by_source)
    entries = [e for s, r in by_source.items() for e in _verified(s, r, candidate)]
    notes = (*coverage.notes(cover), *_cut_notes(by_source.values()))
    read = tuple(_LABEL[s] for s in cover.covered)
    names = _candidate_names(entries)
    exact = [n for n in names if n.casefold() == candidate.casefold()]
    if not chosen and len(names) > 1 and not exact:
        return Collected(notes=notes, options=_options(entries, names), sources_read=read,
                         any_source_failed=bool(cover.failed))
    keep = exact[0] if exact else (names[0] if chosen and len(names) == 1 else "")
    blocks = tuple(b for name, b in entries if not keep or name.casefold() == keep.casefold())
    return Collected(blocks=blocks, notes=notes, sources_read=read, any_source_failed=bool(cover.failed))


def _asked(req: request.Request) -> tuple[str, ...]:
    if req.folder or (req.wants_docs and not req.wants_mail):
        return ("drive",)
    if req.wants_mail and not req.wants_docs:
        return ("mail",)
    return ("mail", "drive")


def _coverage(catalog: Catalog, results: Mapping[str, StepResult]) -> coverage.Coverage:
    offered = {s: catalog.tools_for(s) for s in coverage.SOURCES}
    dropped: dict[str, str] = {}
    overrides: dict[str, coverage.State] = {}
    for source in coverage.SOURCES:
        result = results.get(source)
        if result is None:
            overrides[source] = "not_asked"
            continue
        dropped = {**dropped, **result.dropped}
        if result.status == "skipped":
            overrides[source] = "skipped"
        elif result.status in ("unreadable", "paused"):
            overrides[source] = "unreadable"
        elif result.status == "no_tool":
            overrides[source] = "missing"
    return coverage.assess(offered, dropped, overrides, helpers=frozenset({"recover_compressed"}))


def _verified(source: str, result: StepResult, candidate: str) -> list[tuple[str, Block]]:
    if result.status != "ok":
        return []
    wanted = set(candidate.casefold().split())
    kept: list[tuple[str, Block]] = []
    for raw in items(result.answer, "feedback")[:MAX_ITEMS]:
        name, interviewer = text_of(raw, "candidate"), text_of(raw, "interviewer")
        body = result.seen.kept_sentences(str(raw.get("text") or ""))
        if not (name and interviewer and body) or not (result.seen.has_phrase(name) and result.seen.has_phrase(interviewer)):
            continue
        if not wanted & set(name.casefold().split()):
            continue
        rating, interview = text_of(raw, "rating_text"), text_of(raw, "interview")
        url, day = text_of(raw, "source_url"), text_of(raw, "date")
        kept.append((name, Block(
            interviewer=interviewer, text=body, interview=interview if result.seen.has_phrase(interview) else "",
            rating_text=rating if result.seen.has_phrase(rating) and is_rating(rating) else "", source=_LABEL[source],
            source_url=url if result.seen.has(url) and url.startswith("http") else "",
            date=day if result.seen.has(day) else "")))
    log.info("%s: kept %d checked feedback items", source, len(kept))
    return kept


def _candidate_names(entries: Sequence[tuple[str, Block]]) -> list[str]:
    return list(dict.fromkeys(name for name, _ in entries))


def _options(entries: Sequence[tuple[str, Block]], names: Sequence[str]) -> tuple[tuple[str, str], ...]:
    options = []
    for name in names[:MAX_OPTIONS]:
        counts: dict[str, int] = {}
        for entry_name, block in entries:
            if entry_name == name:
                counts[block.source] = counts.get(block.source, 0) + 1
        found = ", ".join(f"{n} {kind}{'' if n == 1 else 's'}" for kind, n in sorted(counts.items()))
        options.append((f"{name}, {found}", name))
    return tuple(options)


def _cut_notes(results: Sequence[StepResult]) -> tuple[str, ...]:
    notes = []
    cut = sum(r.cut for r in results)
    redacted = sum(r.redacted for r in results)
    if cut:
        notes.append(f"{cut} long item(s) were cut at 12,000 characters; the debrief covers the part read.")
    if redacted:
        notes.append(f"{redacted} compensation line(s) were removed before reading.")
    if any(r.partial for r in results):
        notes.append(prompts.STEP_LIMIT_NOTE)
    return tuple(notes)
