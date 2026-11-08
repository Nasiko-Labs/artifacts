"""Pure: the facts block the model narrates from, and every value it may mention, formatted in code by the
column's own rules (decimals, percent sign, currency prefix, grouping; percentages to one decimal).

The model sees only ids and placeholders (`{m1.cur}`, `{m1.pct}`, `{why:n1}` ...); code substitutes the
formatted values afterwards, so a number in the paragraph can only come from here."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from movers import Delta, Movement
from periods import Selection
from table import Column, Table, is_blank

PCT_DECIMALS = 1
_MAX_NOTE_CHARS = 300


@dataclass(frozen=True)
class Note:
    id: str
    metric: str | None
    text: str
    source: str            # "sheet:<column>:<row>" | "user"


@dataclass(frozen=True)
class MoverFact:
    id: str
    metric: str
    direction: str         # up | down | flat
    word: str              # the direction word code chose: rose | fell | was flat
    cur: str
    prev: str
    delta: str
    pct: str               # "8.2%" or "n/a" when the previous value was zero
    current: float
    previous: float
    delta_value: float
    pct_value: float | None
    rank: int


@dataclass(frozen=True)
class Facts:
    period_current: str
    period_previous: str
    compare: str
    movers: tuple[MoverFact, ...]
    flat: tuple[str, ...]
    notes: tuple[Note, ...]
    excluded_partial: str | None
    few_points: bool
    metric_names: tuple[str, ...]

    @property
    def top(self) -> MoverFact | None:
        return self.movers[0] if self.movers else None

    def notes_for(self, metric: str) -> tuple[Note, ...]:
        """Notes that can explain a metric: ones naming it, plus row-level notes and the user's context."""
        return tuple(n for n in self.notes if n.metric in (None, metric))

    def values(self) -> dict[str, str]:
        """placeholder -> formatted text."""
        found = {"period.current": self.period_current, "period.previous": self.period_previous}
        for m in self.movers:
            found.update({f"{m.id}.cur": m.cur, f"{m.id}.prev": m.prev, f"{m.id}.delta": m.delta,
                          f"{m.id}.pct": m.pct, f"{m.id}.metric": m.metric})
        return found

    def for_model(self) -> str:
        """The JSON the model narrates from: ids, names, direction words and notes; no number at all."""
        return json.dumps({
            "period_current": "{period.current}", "period_previous": "{period.previous}",
            "comparison": "same week last month" if self.compare == "month" else "previous period",
            "movers (largest change first)": [{"id": m.id, "metric": m.metric, "direction_word": m.word,
                        "placeholders": [f"{{{m.id}.cur}}", f"{{{m.id}.prev}}", f"{{{m.id}.delta}}",
                                         f"{{{m.id}.pct}}"]} for m in self.movers],
            "flat_metrics": list(self.flat),
            "notes": [{"marker": f"{{why:{n.id}}}", "about": n.metric or "the whole period",
                       "from": "the user's own context" if n.source == "user" else "the sheet's notes column",
                       "text": n.text} for n in self.notes],
        }, ensure_ascii=False, indent=1)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Facts:
        return Facts(period_current=data["period_current"], period_previous=data["period_previous"],
                     compare=data.get("compare") or "previous",
                     movers=tuple(MoverFact(**m) for m in data.get("movers") or ()),
                     flat=tuple(data.get("flat") or ()), notes=tuple(Note(**n) for n in data.get("notes") or ()),
                     excluded_partial=data.get("excluded_partial"), few_points=bool(data.get("few_points")),
                     metric_names=tuple(data.get("metric_names") or ()))


def build(table: Table, selection: Selection, movement: Movement, context: str | None) -> Facts:
    current = table.rows[selection.current][table.period.index] if selection.current is not None else ""
    previous = table.rows[selection.previous][table.period.index] if selection.previous is not None else ""
    movers = tuple(_mover(d) for d in movement.movers)
    notes = _notes(table, selection, context)
    return Facts(current, previous, selection.compare, movers, movement.flat, notes, selection.excluded_partial,
                 movement.few_points, tuple(c.name for c in table.metrics))


def _mover(d: Delta) -> MoverFact:
    word = {"up": "rose", "down": "fell"}.get(d.direction, "was flat")
    return MoverFact(id=f"m{d.rank}", metric=d.metric, direction=d.direction, word=word,
                     cur=format_value(d.current, d.column), prev=format_value(d.previous, d.column),
                     delta=format_delta(d.delta, d.column), pct=format_pct(d.pct), current=d.current,
                     previous=d.previous, delta_value=d.delta, pct_value=d.pct, rank=d.rank)


def format_value(value: float, column: Column) -> str:
    grouped = column.grouped or abs(value) >= 10_000
    body = f"{abs(value):,.{column.decimals}f}" if grouped else f"{abs(value):.{column.decimals}f}"
    sign = "-" if value < 0 and float(body.replace(",", "")) != 0 else ""
    return f"{sign}{column.prefix}{body}{'%' if column.percent else ''}"


def format_delta(delta: float, column: Column) -> str:
    """The size of the change (no sign: the direction word carries it); percent columns move in points."""
    if column.percent:
        return f"{abs(delta):.{column.decimals}f} pts"
    return format_value(abs(delta), column)


def format_pct(pct: float | None) -> str:
    return "n/a" if pct is None else f"{abs(pct) * 100:.{PCT_DECIMALS}f}%"


def _notes(table: Table, selection: Selection, context: str | None) -> tuple[Note, ...]:
    found: list[Note] = []
    if selection.current is not None:
        row = table.rows[selection.current]
        number = table.row_numbers[selection.current]
        for column in table.notes:
            text = row[column.index].strip()[:_MAX_NOTE_CHARS]
            if not is_blank(text):
                found.append(Note(f"n{len(found) + 1}", _metric_named(text, table), text,
                                  f"sheet:{column.name}:{number}"))
    if context and context.strip():
        found.append(Note(f"n{len(found) + 1}", _metric_named(context, table), context.strip()[:_MAX_NOTE_CHARS],
                          "user"))
    return tuple(found)


def _metric_named(text: str, table: Table) -> str | None:
    named = [c.name for c in table.metrics if re.search(rf"\b{re.escape(c.name)}\b", text, re.IGNORECASE)]
    return named[0] if len(named) == 1 else None
