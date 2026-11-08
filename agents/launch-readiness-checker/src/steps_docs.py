"""Docs step: the launch doc, its modified time, and its text. The text is taken by code from the read calls'
output (search/list results excluded), never from the model, and capped at DOC_TEXT_CHARS."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from functools import partial

import prompts
import records
from config import DOC_TEXT_CHARS, MAX_DOCS
from deps import TurnDeps
from discovery import Catalog, name_tokens
from fence import Record
from reader import StepOutcome, flat_strings, reader_for, run_step
from scope import Request

_LISTING_WORDS = frozenset({"search", "list", "find", "query"})


async def run(deps: TurnDeps, catalog: Catalog | None, request: Request) -> StepOutcome:
    reader = reader_for(deps, catalog, "docs")
    system = prompts.docs_system(request.launch or "", request.hints, deps.today)
    user = f"Find and read the launch doc for {request.launch!r}."
    outcome = await run_step(deps, reader, system, user, partial(records.docs, cap=MAX_DOCS))
    docs = tuple(with_text(d, outcome.records, len(outcome.parsed.rows)) for d in outcome.parsed.rows)
    return replace(outcome, parsed=replace(outcome.parsed, rows=docs))


def with_text(doc: records.Doc, recs: Sequence[Record], doc_count: int) -> records.Doc:
    """The doc's text from the read calls that mention its id (or every read call, when one doc was found)."""
    reads = [r for r in recs if not _LISTING_WORDS & set(name_tokens(r.tool))]
    mine = [r for r in reads if doc.id in r.body] or (reads if doc_count == 1 else [])
    text = "\n".join(_text(r.body) for r in mine).strip()
    capped = any(r.capped for r in mine) or len(text) > DOC_TEXT_CHARS
    return replace(doc, text=text[:DOC_TEXT_CHARS], capped=capped)


def _text(body: str) -> str:
    strings = flat_strings(body)
    return "\n".join(strings) if strings else body
