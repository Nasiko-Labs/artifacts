"""Pure: the table's shape and its facts, in importance order.

Shapes: (a) time_series — a period column with ≥ 4 points; (c) comparison — two numeric columns whose
headers name two periods (2025 / 2026, before / after, last year / this year); (b) ranking — a category
column and numbers; (d) other — no usable label column or a label column full of repeats, read as (b)
with a note. A total row is left out of every fact and checked against the sum of the other rows."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace

from cells import MONTHS, time_key
from fact import FactSet, number
from quality import outliers, quality_facts, total_check, total_rows
from ranking import comparison_facts, ranking_facts
from series import series_facts
from table import Column, Table

MIN_SERIES_POINTS = 4
REPEATED_LABEL_SHARE = 0.2
PERIOD_HEADER = re.compile(
    rf"^(?:(?:19|20)\d{{2}}|fy\s*'?\d{{2,4}}|q[1-4](?:\s*'?\d{{2,4}})?|\d{{4}}\s*q[1-4]|(?:{MONTHS})\.?(?:\s*'?\d{{2,4}})?|"
    r"before|after|last (?:year|month|quarter|week)|this (?:year|month|quarter|week)|previous(?: \w+)?|prior(?: \w+)?|"
    r"current(?: \w+)?|ly|ty|py|cy|old|new|start|end|then|now|baseline)$", re.IGNORECASE)
_PARENS = re.compile(r"\([^)]*\)")
NO_NUMBERS = "I couldn't find a column of numbers in that table, so there is nothing to describe yet."


def sentence_name(name: str) -> str:
    """A column name for a sentence: its unit note dropped ("Revenue (USD K)" → "Revenue"), else as written."""
    return " ".join(_PARENS.sub(" ", name).split()) or name


def is_period_header(name: str) -> bool:
    return bool(PERIOD_HEADER.match(" ".join(_PARENS.sub(" ", name).split())))


def build(table: Table) -> FactSet:
    numeric = table.numeric()
    if not numeric:
        return FactSet("other", (), notes=(*table.notes, NO_NUMBERS))
    label_col = next(iter(table.labels()), None)
    labels = list(label_col.raw) if label_col else [f"Row {n}" for n in range(1, table.rows + 1)]
    notes = list(table.notes)
    totals = total_rows(labels) if label_col else []
    quality = [d for d in (total_check(labels, numeric[0], totals),) if d is not None]
    if totals:
        notes.append(f"I left out the {labels[totals[0]]} row so it isn't counted twice.")
    keep = [i for i in range(len(labels)) if i not in totals]
    labels = [labels[i] for i in keep]
    numeric = [replace(_subset(c, keep), name=sentence_name(c.name)) for c in numeric]
    shape, note = _shape(label_col, labels, numeric)
    if note:
        notes.append(note)
    if shape == "time_series":
        labels, numeric, order_note = _in_time_order(labels, numeric)
        notes += [order_note] if order_note else []
        story = series_facts(labels, numeric) + outliers(labels, numeric[0])
    elif shape == "comparison":
        periods = [c for c in numeric if is_period_header(c.name)]
        before, after = periods[0], periods[-1]
        story = comparison_facts(labels, before, after) + outliers(labels, after)
    else:
        story = ranking_facts(labels, numeric) + outliers(labels, numeric[0])
    quality += quality_facts(labels, numeric, shape == "time_series", len(labels))
    names = [c.name for c in table.columns]
    all_labels = tuple(dict.fromkeys([*(label_col.raw if label_col else labels), *names]))
    return FactSet(shape, number(story, quality), all_labels, tuple(notes), len(labels), numeric[0].name)


def _subset(column: Column, keep: list[int]) -> Column:
    return replace(column, raw=tuple(column.raw[i] for i in keep), values=tuple(column.values[i] for i in keep))


def _shape(label_col: Column | None, labels: list[str], numeric: list[Column]) -> tuple[str, str]:
    if label_col is None:
        return "other", ("I couldn't tell what each row is, so I read the rows in order and ranked them by "
                         f"{numeric[0].name}.")
    if label_col.kind == "time" and len(labels) >= MIN_SERIES_POINTS:
        return "time_series", ""
    if len([c for c in numeric if is_period_header(c.name)]) >= 2:
        return "comparison", ""
    repeats = sum(k - 1 for k in Counter(labels).values())
    if labels and repeats / len(labels) > REPEATED_LABEL_SHARE:
        return "other", (f"This table doesn't match a simple shape (a trend, a ranking or a before/after "
                         f"comparison), so I read it as a ranking of {label_col.name} by {numeric[0].name}.")
    if label_col.kind == "time":
        return "ranking", "There are too few periods for a trend, so I compared them instead."
    return "ranking", ""


def _in_time_order(labels: list[str], numeric: list[Column]) -> tuple[list[str], list[Column], str]:
    keys = [time_key(lbl) for lbl in labels]
    if any(k is None for k in keys) or keys == sorted(keys):
        return labels, numeric, ""
    order = sorted(range(len(labels)), key=lambda i: keys[i])
    note = ("Your table runs newest first; I read it oldest to newest." if order == list(range(len(labels)))[::-1]
            else "I put the periods in date order.")
    return [labels[i] for i in order], [_subset(c, order) for c in numeric], note
