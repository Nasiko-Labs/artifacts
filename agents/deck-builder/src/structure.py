"""Pure: the code half of the deck rules. After the (at most one) repair call, whatever still breaks a rule
is fixed here: placeholders for ungrounded tokens, trimmed bullets, "(cont.)" splits, frame slides, caps."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import checks
from checks import (
    MAX_BULLET_WORDS,
    MAX_BULLETS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    Context,
)
from grounding import count_placeholders, ground_text
from inputs import MAX_SLIDES
from models import ChartRef, Deck, Slide, Stat

log = logging.getLogger("deck_builder.structure")

AGENDA_MIN_SLIDES = 8
CLOSING_TITLE = "Questions and discussion"


@dataclass
class Report:
    placeholders: list[str] = field(default_factory=list)   # "slide 3: [figure?]"
    dropped: int = 0
    split: int = 0
    notes: list[str] = field(default_factory=list)
    repaired: bool = False

    @property
    def capped(self) -> bool:
        return bool(self.notes) or self.split > 0


def enforce(deck: Deck, ctx: Context) -> tuple[Deck, Report]:
    report = Report()
    slides = [_fix_layout(s, ctx, report) for s in deck.slides]
    slides = [_ground(s, ctx) for s in slides]
    kept = [s for s in slides if not checks.needs_facts(s) or checks.grounded_facts(s, ctx)]
    report.dropped = len(slides) - len(kept)
    if report.dropped:
        log.info("dropped ungrounded slides: %s", [s.title[:40] for s in slides if s not in kept])
    slides = _frame(_add_missing_charts(kept, ctx, report), deck.title)
    if len(slides) < AGENDA_MIN_SLIDES:
        slides = [s for s in slides if not checks.is_agenda(s)]
    slides = _split_all(slides, ctx, report)
    if len(slides) > MAX_SLIDES:
        report.notes.append(f"The deck was capped at {MAX_SLIDES} slides; {len(slides) - MAX_SLIDES} were cut.")
        slides = [*slides[:MAX_SLIDES - 1], slides[-1]]
    title, _ = ground_text(deck.title, ctx.corpus)
    fixed = deck.model_copy(update={"slides": slides, "title": title})
    report.placeholders = [f"slide {n}: {p}" for n, s in enumerate(slides, start=1)
                           for p in _placeholders(s)]
    return fixed, report


def _add_missing_charts(slides: list[Slide], ctx: Context, report: Report) -> list[Slide]:
    """A table pasted this turn that no slide uses becomes a native chart slide, titled from values in the table."""
    used = {s.chart.table for s in slides if s.layout == "chart" and s.chart} | {
        s.table.table for s in slides if s.layout == "table" and s.table}
    added = []
    for name in ctx.fresh:
        table = ctx.tables.get(name)
        numeric = [c for c in table.numeric_columns() if table.column(c) != 0] if table else []
        if table is None or name in used or not numeric or len(table.rows) < 2:
            continue
        label, column = table.columns[0], numeric[0]
        idx = table.column(column)
        first, last = table.rows[0], table.rows[-1]
        title = f"{column[:1].upper()}{column[1:]} went from {first[idx]} to {last[idx]} ({first[0]} to {last[0]})"
        added.append(Slide(layout="chart", title=title, chart=ChartRef(table=name, label_column=label,
                                                                        value_columns=numeric[:4])))
        report.notes.append(f"Added a chart of table {name} ({', '.join(numeric[:4])} by {label}).")
    if not added:
        return slides
    closing = [s for s in slides if s.layout == "closing"]
    body = [s for s in slides if s.layout != "closing"]
    return [*body, *added, *closing]


def _placeholders(slide: Slide) -> list[str]:
    visible = [slide.title, *slide.bullets, *slide.right, slide.quote,
               *([slide.stat.value, slide.stat.caption] if slide.stat else [])]
    found = []
    for text in visible:
        for mark in ("[figure?]", "[name?]", "[date?]"):
            found.extend([mark] * text.count(mark))
    return found


def _fix_layout(slide: Slide, ctx: Context, report: Report) -> Slide:
    reason = None
    if slide.layout == "chart":
        reason = checks.chart_problem(slide, ctx)
    elif slide.layout == "table":
        reason = checks.table_problem(slide, ctx)
    elif slide.layout == "quote" and not ctx.clean_corpus.has_fragment(slide.quote):
        reason = "the quote is not in your notes"
    elif slide.layout == "big_number" and not slide.stat:
        reason = "no stat was given"
    if reason is None:
        return slide
    report.notes.append(f"A {slide.layout} slide (\"{slide.title[:60]}\") became a bullet slide: {reason}.")
    return slide.model_copy(update={"layout": "bullets", "chart": None, "table": None, "quote": ""})


def _ground(slide: Slide, ctx: Context) -> Slide:
    def g(text: str) -> str:
        return ground_text(text, ctx.corpus)[0]

    def trim(text: str) -> str:
        words = g(text).split()
        return " ".join(words[:MAX_BULLET_WORDS])

    stat = Stat(value=g(slide.stat.value), caption=g(slide.stat.caption)) if slide.stat else None
    return slide.model_copy(update={"title": g(slide.title)[:checks.MAX_TITLE_CHARS],
                                    "bullets": [trim(b) for b in slide.bullets],
                                    "right": [trim(b) for b in slide.right[:MAX_BULLETS]],
                                    "notes": g(slide.notes), "quote": g(slide.quote), "stat": stat})


def _frame(slides: list[Slide], title: str) -> list[Slide]:
    body = [s for i, s in enumerate(slides) if not (s.layout == "title" and i > 0)]
    if not body or body[0].layout != "title":
        body = [Slide(layout="title", title=title), *body]
    closings = [s for s in body if s.layout == "closing"]
    body = [s for s in body if s.layout != "closing"]
    return [*body, closings[-1] if closings else Slide(layout="closing", title=CLOSING_TITLE)]


def _split_all(slides: list[Slide], ctx: Context, report: Report) -> list[Slide]:
    out: list[Slide] = []
    for slide in slides:
        parts = _split(slide, ctx, report)
        report.split += len(parts) - 1
        out.extend(parts)
    return out


def _split(slide: Slide, ctx: Context, report: Report) -> list[Slide]:
    if slide.layout == "table" and slide.table is not None:
        return _split_table(slide, ctx, report)
    if len(slide.bullets) <= MAX_BULLETS:
        return [slide]
    groups = [slide.bullets[i:i + MAX_BULLETS] for i in range(0, len(slide.bullets), MAX_BULLETS)]
    first = slide.model_copy(update={"bullets": groups[0]})
    rest = [slide.model_copy(update={"bullets": g, "title": f"{slide.title} (cont.)", "facts": slide.facts})
            for g in groups[1:]]
    return [first, *rest]


def _split_table(slide: Slide, ctx: Context, report: Report) -> list[Slide]:
    table = ctx.tables[slide.table.table]
    columns = [c for c in slide.table.columns if table.column(c) is not None] or list(table.columns)
    if len(columns) > MAX_TABLE_COLS:
        report.notes.append(f"Table {table.name} has {len(columns)} columns; slides show the first {MAX_TABLE_COLS}.")
        columns = columns[:MAX_TABLE_COLS]
    total = len(table.rows)
    parts = []
    for start in range(0, max(total, 1), MAX_TABLE_ROWS):
        ref = slide.table.model_copy(update={"columns": columns, "rows_from": start,
                                             "rows_to": min(total, start + MAX_TABLE_ROWS)})
        title = slide.title if start == 0 else f"{slide.title} (cont.)"
        parts.append(slide.model_copy(update={"table": ref, "title": title}))
    return parts


def placeholder_total(deck: Deck) -> int:
    return sum(count_placeholders(t) for s in deck.slides for t in checks.slide_texts(s))
