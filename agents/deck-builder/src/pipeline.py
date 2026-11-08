"""check → (one repair call) → enforce in code → build → verify. Returns everything the reply needs."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import build_pptx
import checks
import outline
from checks import Context
from dataparse import Table
from deps import TurnDeps
from grounding import build_corpus
from llm import Budget
from models import Deck, Theme
from structure import Report, enforce
from verify_pptx import Verified, verify

log = logging.getLogger("deck_builder.pipeline")

MAX_FILE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Built:
    deck: Deck
    report: Report
    data: bytes
    verified: Verified
    chart_failures: int


def context(parts: list[str], injected: list[str], tables: dict[str, Table], target: int, given: list[str],
            fresh: tuple[str, ...] = ()) -> Context:
    cells = [" ".join([*t.columns, *(c for r in t.rows for c in r)]) for t in tables.values()]
    everything = [*parts, *cells, *given]
    clean = [p for p in everything if p not in injected]
    clean_text = "\n".join(clean)
    for line in injected:
        clean_text = clean_text.replace(line, "")
    return Context(corpus=build_corpus(everything), clean_corpus=build_corpus([clean_text]), tables=tables,
                   target=target, notes_text="\n".join(parts), fresh=fresh)


async def finish(deps: TurnDeps, budget: Budget, deck: Deck, ctx: Context, theme: Theme, tables_info: list[dict],
                 given: list[str]) -> Built:
    deck = deck.model_copy(update={"theme": theme})
    problems = checks.violations(deck, ctx)
    repaired = False
    if problems and budget.can_call():
        log.info("asking for one repair: %d problems", len(problems))
        fixed = await outline.repair_deck(deps, budget, deck, problems, tables_info, given)
        if fixed is not None:
            deck, repaired = fixed.model_copy(update={"theme": theme}), True
    chart_failures = sum(1 for s in deck.slides if s.layout == "chart" and checks.chart_problem(s, ctx))
    final, report = enforce(deck, ctx)
    report.repaired = repaired
    data = build_pptx.build(final, ctx.tables)
    verified = verify(data, final)
    if not verified.ok:
        log.warning("built deck failed verification: %s", "; ".join(verified.problems))
    return Built(final, report, data, verified, chart_failures)
