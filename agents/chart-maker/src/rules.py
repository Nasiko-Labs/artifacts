"""Pure: the chart-type rule table, caps, sorting and top-N grouping. Code decides the type; the
model's suggestion only breaks ties, and an explicit user ask is honoured unless the data forbids it."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from dataset import Dataset

MAX_DATA_CHARS = 20_000
MAX_ROWS = 2_000
MAX_SERIES = 12
MAX_BAR_CATEGORIES = 12
MAX_COLUMN_CATEGORIES = 8
MAX_STACK_SERIES = 4
MAX_PIE_SLICES = 5
TYPES = ("bar", "column", "line", "stacked", "share")

_EXPLICIT = (
    ("stacked", re.compile(r"\bstack(?:ed)?\b|\bparts? of (?:a |the )?whole\b", re.IGNORECASE)),
    ("share", re.compile(r"\bpie\b|\bdonut\b|\bshares?\b|\bpercent(?:age)?s?\b|%", re.IGNORECASE)),
    ("line", re.compile(r"\bline(?: chart| graph)?\b|\btrend\b|\bover time\b", re.IGNORECASE)),
    ("column", re.compile(r"\bcolumns?(?: chart)?\b|\bvertical\b", re.IGNORECASE)),
    ("bar", re.compile(r"\bbar(?: chart| graph)?s?\b|\bhorizontal\b", re.IGNORECASE)),
)
_PIE = re.compile(r"\bpie\b|\bdonut\b", re.IGNORECASE)


@dataclass(frozen=True)
class Choice:
    type: str
    notes: tuple[str, ...] = ()
    overridden: bool = False   # an explicit user request had to be changed
    mermaid_pie: bool = False


def explicit_type(ask: str) -> str | None:
    for name, pattern in _EXPLICIT:
        if pattern.search(ask or ""):
            return name
    return None


def size_problem(data_chars: int, rows: int, series: int) -> str | None:
    """A refusal reason when the data is over a cap (stated, never truncated)."""
    if data_chars > MAX_DATA_CHARS:
        return (f"That data is {data_chars:,} characters; I chart up to {MAX_DATA_CHARS:,}. Filter or "
                f"aggregate it first (for example one row per month) and paste it again.")
    if rows > MAX_ROWS:
        return f"That data has {rows:,} rows; I chart up to {MAX_ROWS:,}. Filter or aggregate it and paste it again."
    if series > MAX_SERIES:
        return (f"That's {series} value columns; a readable chart has at most {MAX_SERIES}. Tell me which "
                f"columns to chart, or paste fewer.")
    return None


def choose(ds: Dataset, ask: str, model_type: str | None = None) -> Choice:
    wanted = explicit_type(ask)
    default = _default(ds, model_type)
    if wanted is None:
        return Choice(default)
    allowed, reason = _allowed(ds, wanted)
    pie = bool(_PIE.search(ask or "")) and wanted == "share"
    if allowed and pie and len(ds.labels) > MAX_PIE_SLICES:
        note = f"A pie of {len(ds.labels)} slices is unreadable, so this is a ranked percentage bar instead."
        return Choice("share", (note,), overridden=True)
    if allowed:
        return Choice(wanted, mermaid_pie=pie)
    return Choice(default, (f"I drew a {default} chart instead of a {wanted} chart: {reason}.",), overridden=True)


def _default(ds: Dataset, model_type: str | None) -> str:
    n_series, n_cats = len(ds.series), len(ds.labels)
    if ds.ordinal and n_cats >= 3:
        return "line"
    if n_series == 1:
        if model_type == "column" and n_cats <= MAX_COLUMN_CATEGORIES:
            return "column"
        return "bar"
    if n_series <= MAX_STACK_SERIES and n_cats <= MAX_COLUMN_CATEGORIES:
        return "column"
    return "bar"


def _allowed(ds: Dataset, wanted: str) -> tuple[bool, str]:
    if wanted in ("stacked", "share") and ds.has_negative:
        return False, "the data has negative values, which can't be parts of a whole"
    if wanted == "share" and len(ds.series) > 1:
        return False, "a share chart shows one value column"
    if wanted == "stacked" and len(ds.series) < 2:
        return False, "stacking needs two or more value columns"
    if wanted == "stacked" and (len(ds.series) > MAX_STACK_SERIES or len(ds.labels) > MAX_COLUMN_CATEGORIES):
        return False, f"stacks stay readable up to {MAX_STACK_SERIES} series and {MAX_COLUMN_CATEGORIES} categories"
    if wanted == "column" and len(ds.labels) > MAX_COLUMN_CATEGORIES:
        return False, f"columns stay readable up to {MAX_COLUMN_CATEGORIES} categories"
    if wanted == "line" and len(ds.labels) < 2:
        return False, "a line needs at least two points"
    return True, ""


def arrange(ds: Dataset, chart_type: str, sort: str | None, top_n: int | None) -> tuple[Dataset, tuple[str, ...]]:
    """Sorted and capped for drawing; every fold into "Other" is stated with its arithmetic."""
    out = ds
    if sort == "label":
        out = _reorder(out, sorted(range(len(out.labels)), key=lambda i: out.labels[i].casefold()))
    elif chart_type != "line" and (sort == "value" or (sort is None and not out.ordinal)):
        out = _reorder(out, sorted(range(len(out.labels)), key=lambda i: -_row_total(out, i)))
    limit = top_n if top_n else (None if chart_type == "line" else MAX_BAR_CATEGORIES)
    if limit is None or len(out.labels) <= limit:
        return out, ()
    return _fold(out, limit, top_n is not None)


def _row_total(ds: Dataset, i: int) -> float:
    return sum(s[i] for s in ds.values)


def _reorder(ds: Dataset, order: list[int]) -> Dataset:
    return replace(ds, labels=tuple(ds.labels[i] for i in order),
                   values=tuple(tuple(s[i] for i in order) for s in ds.values))


def _fold(ds: Dataset, limit: int, asked: bool) -> tuple[Dataset, tuple[str, ...]]:
    ranked = sorted(range(len(ds.labels)), key=lambda i: -_row_total(ds, i))
    keep = sorted(ranked[:limit])  # the current order (value, label or time) is kept
    rest = ranked[limit:]
    rest_sums = tuple(sum(s[i] for i in rest) for s in ds.values)
    label = f"Other ({len(rest)})"
    values = tuple(tuple(s[i] for i in keep) + (rest_sums[k],) for k, s in enumerate(ds.values))
    folded = replace(ds, labels=tuple(ds.labels[i] for i in keep) + (label,), values=values,
                     other_count=len(rest), total_categories=len(ds.labels))
    why = "" if asked else f" (a readable bar chart has at most {MAX_BAR_CATEGORIES})"
    note = (f"Showing the top {limit} of {len(ds.labels)} categories{why}; the rest are grouped as {label}, "
            f"which adds up their values.")
    return folded, (note,)
