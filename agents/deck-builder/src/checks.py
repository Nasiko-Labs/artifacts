"""Pure: the deck rules checked in code. `violations` lists what the one repair call is asked to fix;
`enforce` (structure.py) fixes the rest in code. Nothing here calls the model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dataparse import Table
from grounding import NUMBER, Corpus, ground_text
from models import FRAME_LAYOUTS, Deck, Slide

MAX_TITLE_CHARS = 90
MAX_BULLETS = 5
MAX_BULLET_WORDS = 14
MAX_TABLE_COLS = 6
MAX_TABLE_ROWS = 8
MAX_CHART_CATEGORIES = 12
MAX_CHART_SERIES = 4
BULLET_OVERLAP = 0.6
FACT_EXEMPT = frozenset({"title", "closing", "chart", "table"})


@dataclass(frozen=True)
class Context:
    corpus: Corpus
    clean_corpus: Corpus                  # without lines that read as injected instructions
    tables: dict[str, Table] = field(default_factory=dict)
    target: int = 8
    notes_text: str = ""
    fresh: tuple[str, ...] = ()           # tables pasted this turn: each gets a chart if the model left it out


def is_agenda(slide: Slide) -> bool:
    return "agenda" in slide.title.casefold()


def needs_facts(slide: Slide) -> bool:
    return slide.layout not in FACT_EXEMPT and not is_agenda(slide)


def grounded_facts(slide: Slide, ctx: Context) -> int:
    """Verbatim facts behind the slide; failing that, a bullet whose words are nearly all in the notes counts."""
    facts = sum(1 for f in slide.facts if ctx.clean_corpus.has_fragment(f.source))
    texts = [slide.title, *slide.bullets, *slide.right, *([slide.stat.value] if slide.stat else [])]
    return facts or sum(1 for t in texts if _mostly_in_notes(t, ctx) or _has_grounded_number(t, ctx))


def _has_grounded_number(text: str, ctx: Context) -> bool:
    return any(ctx.clean_corpus.has_number(n) for n in NUMBER.findall(text) if len(n.strip("$€£%")) > 1)


def _mostly_in_notes(text: str, ctx: Context) -> bool:
    words = [w for w in re.findall(r"[a-z0-9']+", text.casefold()) if len(w) > 3]
    return len(words) >= 2 and sum(ctx.clean_corpus.has_word(w) for w in words) / len(words) >= BULLET_OVERLAP


def slide_texts(slide: Slide) -> list[str]:
    stat = [slide.stat.value, slide.stat.caption] if slide.stat else []
    return [slide.title, *slide.bullets, *slide.right, slide.notes, slide.quote, *stat]


def chart_problem(slide: Slide, ctx: Context) -> str | None:
    ref = slide.chart
    if ref is None:
        return "has no table reference"
    table = ctx.tables.get(ref.table)
    if table is None:
        return f"references table {ref.table}, which does not exist"
    if table.column(ref.label_column) is None:
        return f"label column {ref.label_column!r} is not in {ref.table}"
    numeric = {c.casefold() for c in table.numeric_columns()}
    values = [c for c in ref.value_columns if c.casefold() in numeric]
    return None if values else f"no numeric value column of {ref.table} is named"


def table_problem(slide: Slide, ctx: Context) -> str | None:
    if slide.table is None or slide.table.table not in ctx.tables:
        return "references no parsed table"
    return None


def violations(deck: Deck, ctx: Context) -> list[str]:
    found: list[str] = []
    for n, slide in enumerate(deck.slides, start=1):
        found.extend(f"slide {n}: {p}" for p in _slide_violations(slide, ctx))
    if deck.slides and deck.slides[0].layout != "title":
        found.append("slide 1 must use the title layout")
    if deck.slides and deck.slides[-1].layout != "closing":
        found.append("the last slide must use the closing layout")
    return found


def _slide_violations(slide: Slide, ctx: Context) -> list[str]:
    found: list[str] = []
    if len(slide.title) > MAX_TITLE_CHARS:
        found.append(f"title is over {MAX_TITLE_CHARS} characters")
    if slide.layout not in FRAME_LAYOUTS and not is_agenda(slide) and len(slide.title.split()) <= 2:
        found.append("title is a topic label; write an action title (a full-sentence claim)")
    if len(slide.bullets) > MAX_BULLETS or len(slide.right) > MAX_BULLETS:
        found.append(f"more than {MAX_BULLETS} bullets")
    if any(len(b.split()) > MAX_BULLET_WORDS for b in [*slide.bullets, *slide.right]):
        found.append(f"a bullet is over {MAX_BULLET_WORDS} words")
    for text in slide_texts(slide):
        if text and ground_text(text, ctx.corpus)[1]:
            found.append(f"uses a number, name or date not in the notes: {text[:80]!r}")
    if needs_facts(slide) and not grounded_facts(slide, ctx):
        found.append("no fact on this slide is a verbatim fragment of the notes")
    if slide.layout == "chart" and (problem := chart_problem(slide, ctx)):
        found.append(f"chart {problem}")
    if slide.layout == "table" and (problem := table_problem(slide, ctx)):
        found.append(f"table {problem}")
    if slide.layout == "quote" and not ctx.clean_corpus.has_fragment(slide.quote):
        found.append("the quote does not appear word for word in the notes")
    return found
