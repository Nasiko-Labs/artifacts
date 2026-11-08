"""The graph's model-calling nodes: compose (map + new deck, or edit) and finish (check, repair, build,
verify, render). Also rebuilding the stored deck for commands that need no model call."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import inputs
import mapping
import outline
import pipeline
import prompts
import render
import script_export
import smalltalk
from charts import given_changes
from dataparse import Table
from inputs import DEFAULT_SLIDES, Parsed
from models import Deck
from session import Built, Stored, capped_parts, report_to_dict, tables_from, theme_of
from theme import theme_from_text

log = logging.getLogger("deck_builder.compose")

EDIT_NOTES_CHARS = 12_000


@dataclass
class Work:
    parsed: Parsed
    deck: Deck | None = None
    edited: bool = False


def _tables(state: dict, parsed: Parsed) -> dict[str, Table]:
    return {**tables_from(state), **{t.name: t for t in parsed.tables}}


async def compose(state: dict, scope) -> dict:
    work: Work = scope.work
    parsed = work.parsed
    tables = _tables(state, parsed)
    infos = [t.to_dict() for t in tables.values()]
    given = given_changes(tables)
    stored = Deck.model_validate(state["deck"]) if state.get("deck") else None
    if stored is not None:
        instruction = parsed.request + (f"\n{parsed.notes}" if parsed.notes else "")
        notes = (state.get("notes") or "")[:EDIT_NOTES_CHARS]
        answer = await outline.edit_deck(scope.deps, scope.budget, stored, instruction, infos, given, notes)
        if isinstance(answer, Deck):
            answer = keep_untouched(stored, answer, inputs.slide_refs(parsed.request))
            work.edited = True
    else:
        notes, kind = parsed.notes, "notes"
        if parsed.chunks:
            notes, kind = await mapping.map_points(scope.deps, scope.budget, parsed.chunks), "points"
        count = parsed.slide_count or DEFAULT_SLIDES
        brief = outline.Brief(parsed.request, notes, kind, infos, given, count)
        answer = await outline.new_deck(scope.deps, scope.budget, brief)
    if answer == outline.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if not isinstance(answer, Deck):
        return {"reply": prompts.COULD_NOT_BUILD, "route": ""}
    work.deck = answer
    return {"route": "finish"}


def keep_untouched(old: Deck, new: Deck, refs: tuple[int, ...]) -> Deck:
    """When the instruction names slides and the count is unchanged, every other slide stays as it was."""
    if not refs or len(old.slides) != len(new.slides):
        return new
    slides = [new.slides[i] if i + 1 in refs else old.slides[i] for i in range(len(old.slides))]
    reverted = sum(1 for i in range(len(slides)) if i + 1 not in refs and new.slides[i] != old.slides[i])
    if reverted:
        log.info("reverted %d slides the edit was not asked to change", reverted)
    return new.model_copy(update={"slides": slides})


async def finish(state: dict, scope) -> dict:
    work: Work = scope.work
    parsed = work.parsed
    tables = _tables(state, parsed)
    given = given_changes(tables)
    parts = capped_parts([*(state.get("parts") or []), scope.text])
    injected = [*(state.get("injected") or []), *parsed.injected]
    theme, theme_notes = theme_from_text(scope.text)
    theme = theme if theme_notes or "#" in scope.text else theme_of(dict(state))
    target = parsed.slide_count or len(work.deck.slides)
    ctx = pipeline.context(parts, injected, tables, target, given, tuple(t.name for t in parsed.tables))
    built = await pipeline.finish(scope.deps, scope.budget, work.deck, ctx, theme,
                                  [t.to_dict() for t in tables.values()], given)
    name = state.get("file_name") or render.file_name(built.deck)
    stored = Stored(built.deck, tables, built.report, name, inputs.folder_name(scope.text) or state.get("folder"),
                    built.chart_failures)
    scope.built = Built(stored, built.data, built.verified.sha256)
    notes = [n for n in (parsed.notes_note, *theme_notes, *built.report.notes, *_table_notes(parsed)) if n]
    if built.report.dropped:
        notes.append(f"{built.report.dropped} slide(s) had nothing from your notes behind them and were left out.")
    reply = _reply(built.deck, built.report, notes, scope.upload, work.edited)
    return {"deck": built.deck.dump(), "tables": [t.to_dict() for t in tables.values()], "parts": parts,
            "injected": injected, "notes": parsed.notes or state.get("notes") or "", "theme": theme.model_dump(),
            "file_name": name, "folder": stored.folder, "report": report_to_dict(built.report),
            "chart_failures": built.chart_failures, "reply": reply, "route": ""}


def _table_notes(parsed: Parsed) -> list[str]:
    return [f"Table {t.name} had {len(t.rows) + t.truncated} rows; I used the first {len(t.rows)}."
            for t in parsed.tables if t.truncated]


def _reply(deck: Deck, report, notes: list[str], upload: bool, edited: bool) -> str:
    lead = "Deck updated" if edited else "Deck built"
    sections = [render.outline(deck, report, lead=lead)]
    if notes:
        sections.append(render.notes_block(notes))
    if not upload:
        sections.append(render.delivery_hint(False))
    return "\n\n".join(sections)


def rebuild(stored: Stored, state: dict) -> Built:
    data, sha = stored.build()
    name = state.get("file_name") or stored.file_name or render.file_name(stored.deck)
    folder = state.get("folder") if "folder" in state else stored.folder
    return Built(Stored(stored.deck, stored.tables, stored.report, name, folder, stored.chart_failures), data, sha)


def script_for(stored: Stored, state: dict) -> str:
    name = state.get("file_name") or stored.file_name or render.file_name(stored.deck)
    return script_export.script(stored.deck, stored.tables, name)
