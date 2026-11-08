"""Turn 1: deck -> extract claims -> sheets (find, maybe ask which, read) -> index -> match -> compare ->
render. Also the follow-up paths that reuse what was found: the sheet pick's resume, a re-check after the
sheet changed, and a re-compare in strict or loose mode (code only, no new pull)."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import cellindex
import checks
import compare
import deck as deck_
import extract
import match
import nasiko_hitl
import prompts
import render
import sheets
import slides
import smalltalk
from deps import TurnDeps
from discovery import Catalog, discover
from records import Cell, Claim, Match
from request import Request

log = logging.getLogger("board_deck.pipeline")

NO_SOURCE_NOTE = "not verified (no source access)"
MAX_HINTS = 3
_FOOTNOTE = re.compile(r"\bsource\s*:\s*([^\n]{3,80})", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(?:deck|slides?|board|sheets?|spreadsheets?|claims?|figures?|numbers?|fact|check|"
                     r"metrics?|arr|revenue|kpis?|finance|source|verify|report)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    reply: str
    envelope: dict | None = None
    question: dict | None = None   # {"metadata": question_metadata} for the single-select pause
    stored: dict | None = None     # what follow-ups are answered from
    pending: dict | None = None    # the paused sheet pick


@dataclass(frozen=True)
class Found:
    """Where the claims came from, carried to the end of the turn."""

    title: str
    slide_count: int
    claims: tuple[Claim, ...]
    notes: tuple[str, ...]
    mode: str
    json_mode: bool
    message: str
    hints: tuple[str, ...] = ()  # the deck's own "Source: ..." footnotes, for the sheet search

    def to_dict(self) -> dict:
        return {"title": self.title, "slide_count": self.slide_count, "claims": [c.to_dict() for c in self.claims],
                "notes": list(self.notes), "mode": self.mode, "json_mode": self.json_mode,
                "message": self.message[:2_000], "hints": list(self.hints)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Found:
        return Found(str(data.get("title") or "the deck"), int(data.get("slide_count") or 0),
                     tuple(Claim.from_dict(c) for c in data.get("claims") or ()), tuple(data.get("notes") or ()),
                     str(data.get("mode") or "default"), bool(data.get("json_mode")), str(data.get("message") or ""),
                     tuple(data.get("hints") or ()))


def refusal(reply: str, message: str) -> str:
    """A fixed refusal that ends the turn before any model call still declines an off-topic message."""
    return reply if _DOMAIN.search(message or "") else smalltalk.OFF_TOPIC_REPLY


async def check(deps: TurnDeps, req: Request) -> Outcome:
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    catalog: Catalog | None = None
    if not (req.deck_text or req.claims) or not req.source_rows:
        catalog, failure = await discover(deps)
        if failure is not None and not (req.deck_text or req.claims):
            return Outcome(refusal(failure, req.message))
    found = await _claims(deps, req, catalog)
    if isinstance(found, Outcome):
        return found
    return await _with_source(deps, req, catalog, found)


async def _claims(deps: TurnDeps, req: Request, catalog: Catalog | None) -> Found | Outcome:
    base = {"mode": req.mode, "json_mode": req.json_mode, "message": req.message}
    if req.claims:
        claims = checks.envelope_claims(req.claims)
        if not claims:
            return Outcome(prompts.NO_CLAIMS)
        return Found("claims from an earlier run", len({c.slide for c in claims}), claims,
                     ("Claims re-checked as given; the deck itself was not re-read.",), **base)
    if req.deck_text:
        deck = slides.split(req.deck_text, "pasted deck")
    else:
        deck_or_reply = await _read_deck(deps, req, catalog)
        if isinstance(deck_or_reply, Outcome):
            return deck_or_reply
        deck = deck_or_reply
    deck = slides.only(deck, req.slide_range)
    extraction = await extract.run(deps, deck)
    if extraction.off_topic:
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    notes = (*_deck_notes(deck, req), *_extraction_notes(extraction))
    if not extraction.claims:
        return Outcome("\n\n".join([prompts.NO_CLAIMS, *notes]))
    return Found(deck.title, len(deck.slides), extraction.claims, notes, **base, hints=footnotes(deck.text))


async def _read_deck(deps: TurnDeps, req: Request, catalog: Catalog | None) -> slides.Deck | Outcome:
    if catalog is None or not catalog.tools_for("deck"):
        return Outcome(refusal(prompts.NEED_DECK, req.message))
    got = await deck_.read(deps, catalog, req.message, req.deck_urls)
    if got.status == "ok" and got.deck is not None:
        return got.deck
    if got.status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    if got.status == "paused":
        return Outcome(prompts.READ_NEEDS_APPROVAL)
    reasons = sorted(set((got.step.dropped if got.step else {}).values()))
    lines = [f"File access isn't available for your connection ({', '.join(reasons)})."] if reasons else []
    return Outcome("\n\n".join([prompts.DECK_NOT_FOUND, *lines]))


def _deck_notes(deck: slides.Deck, req: Request) -> tuple[str, ...]:
    lines = [f"Deck read: {len(deck.slides)} slides, {len(deck.text):,} characters."]
    if not deck.delimited:
        lines.append("No slide markers found: \"slide\" numbers are sections of the text, in order.")
    if deck.chars_cut:
        lines.append(f"Deck text past {slides.MAX_DECK_CHARS:,} characters was not read ({deck.chars_cut:,} more).")
    if req.slide_range:
        lines.append(f"Only slides {req.slide_range[0]} to {req.slide_range[1]} were checked, as asked.")
    lines.append("Numbers inside images or charts are not read.")
    return tuple(lines)


def _extraction_notes(extraction: extract.Extraction) -> tuple[str, ...]:
    lines = []
    if extraction.dropped:
        lines.append(f"{extraction.dropped} extracted figure(s) were dropped: their quote was not found verbatim "
                     "in the deck or they were dates, years or list numbers.")
    if extraction.capped:
        lines.append(f"Claim cap of {checks.MAX_CLAIMS} reached: {extraction.capped} more claim(s) not checked.")
    if extraction.not_checked:
        shown = ", ".join(str(n) for n in extraction.not_checked)
        lines.append(prompts.TIME_LIMIT_NOTE.format(what=f"slides {shown}"))
    return tuple(lines)


def footnotes(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(1).strip() for m in _FOOTNOTE.finditer(text or "")))[:MAX_HINTS]


async def _with_source(deps: TurnDeps, req: Request, catalog: Catalog | None, found: Found) -> Outcome:
    if req.source_rows:
        cells = sheets.pasted_cells(req.source_rows)
        source = f"pasted source rows ({len(cells)})"
        return await finish(deps, found, cells, source, (), bool(cells), True, ())
    if catalog is None or not catalog.tools_for("sheet"):
        return await finish(deps, found, (), "", (prompts.NO_SHEET_ACCESS,), False, False, ())
    targets = tuple(sheets.Target(name=u, url=u) for u in req.sheet_urls)
    if not targets:
        hints = [h for h in (*found.hints, found.title) if h not in ("the deck", "pasted deck")]
        step, candidates = await sheets.find(deps, catalog, req.message, hints)
        if step.status == "off_topic":
            return Outcome(smalltalk.OFF_TOPIC_REPLY)
        chosen = sheets.choose(candidates, req.message)
        if chosen is None:
            return _ask_which(found, candidates)
        if not chosen:
            note = "No source spreadsheet was found; name it or send its link."
            return await finish(deps, found, (), "", (note,), False, False, ())
        targets = chosen
    return await read_and_finish(deps, catalog, found, targets)


def _ask_which(found: Found, candidates: Sequence[sheets.Target]) -> Outcome:
    options = [(t.name[:200], ", ".join(p for p in (f"owner {t.owner}" if t.owner else "",
                                                     f"modified {t.modified}" if t.modified else "") if p))
               for t in candidates]
    metadata = nasiko_hitl.question_metadata(prompts.PICK_HEADER, options, multi_select=False,
                                             allow_custom_input=True)
    listing = "\n".join(f"- {t.name}" for t in candidates)
    pending = {"found": found.to_dict(), "candidates": [t.to_dict() for t in candidates]}
    return Outcome(f"{prompts.PICK_TEXT}\n\n{listing}\n\nOr send a spreadsheet link.", question={"metadata": metadata},
                   pending=pending)


async def resume(deps: TurnDeps, pending: Mapping[str, Any], answer: str) -> Outcome:
    candidates = tuple(sheets.Target.from_dict(t) for t in pending.get("candidates") or ())
    target = sheets.pick(answer, candidates)
    if target is None:
        return Outcome(prompts.NO_PICK)
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome(failure)
    return await read_and_finish(deps, catalog, Found.from_dict(pending.get("found") or {}), (target,))


async def read_and_finish(deps: TurnDeps, catalog: Catalog, found: Found,
                          targets: Sequence[sheets.Target]) -> Outcome:
    read = await sheets.read(deps, catalog, found.message, targets)
    if read.status == "paused":
        return Outcome(prompts.READ_NEEDS_APPROVAL)
    cells = cellindex.build(read.tabs)
    notes = list(read.notes)
    if read.status != "ok":
        reasons = sorted(set(read.dropped.values()))
        why = f" ({', '.join(reasons)})" if reasons else ""
        notes.append(f"The spreadsheet tools returned no cell values I could read{why}; claims are not verified.")
    source = ", ".join(_source_label(n, [t.tab for t in read.tabs if t.spreadsheet == n]) for n in read.names)
    fully = not any(t.cut for t in read.tabs) and not read.notes
    return await finish(deps, found, cells, source, tuple(notes), bool(cells), fully, targets)


def _source_label(name: str, tabs: Sequence[str]) -> str:
    return f"{name} ({len(tabs)} tab{'' if len(tabs) == 1 else 's'} read: {', '.join(tabs)})"


async def finish(deps: TurnDeps, found: Found, cells: Sequence[Cell], source: str, notes: Sequence[str],
                 sheet_read: bool, fully_read: bool, targets: Sequence[sheets.Target]) -> Outcome:
    if cells and deps.traceparent:
        matches = await match.run(deps, found.claims, cells)
    else:
        matches = {c.claim_id: Match(c.claim_id, note=NO_SOURCE_NOTE) for c in found.claims}
    used = {i for m in matches.values() for i in (*m.cell_ids, *m.operands)}
    stored = {"found": found.to_dict(), "cells": [c.to_dict() for c in cells if c.cell_id in used],
              "matches": [m.to_dict() for m in matches.values()], "source": source,
              "notes": [*found.notes, *notes], "sheet_read": sheet_read, "fully_read": fully_read,
              "targets": [t.to_dict() for t in targets]}
    return rendered(stored, found.mode, found.json_mode)


def report_of(stored: Mapping[str, Any], mode: str) -> render.Report:
    found = Found.from_dict(stored.get("found") or {})
    cells = {c["cell_id"]: Cell.from_dict(c) for c in stored.get("cells") or ()}
    matches = {m["claim_id"]: Match.from_dict(m) for m in stored.get("matches") or ()}
    comparisons = tuple(compare.compare(c, matches.get(c.claim_id, Match(c.claim_id)), cells, mode)
                        for c in found.claims)
    return render.Report(found.title, found.slide_count, str(stored.get("source") or ""), comparisons,
                         checks.inconsistencies(found.claims), render.coverage_lines(stored.get("notes") or ()),
                         mode, bool(stored.get("sheet_read")), bool(stored.get("fully_read")))


def rendered(stored: Mapping[str, Any], mode: str, json_mode: bool) -> Outcome:
    report = report_of(stored, mode)
    env = render.envelope(report)
    reply = render.json_block(env) if json_mode else render.markdown(report)
    return Outcome(reply, env, stored={**dict(stored), "mode": mode})


async def recheck(deps: TurnDeps, stored: Mapping[str, Any]) -> Outcome:
    """The sheet changed: re-read the same spreadsheets for the stored claims (the deck is not re-read)."""
    found = Found.from_dict(stored.get("found") or {})
    targets = tuple(sheets.Target.from_dict(t) for t in stored.get("targets") or ())
    if not targets:
        return Outcome("To re-check, paste the updated source rows (Source: metric, period, value) or name the "
                       "spreadsheet.")
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome(failure)
    return await read_and_finish(deps, catalog, found, targets)
