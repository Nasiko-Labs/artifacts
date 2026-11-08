"""Pure: the events of one check, all computed in code (no model).

- `state_change`: the state column's value differs from the snapshot (trimmed, ignoring case).
- `row_changed`: the row's hash differs but its state did not change (one low-priority count).
- `new_row` / `removed_row`: the key is only in this check / only in the snapshot.
- `date_approaching`: a date in [today, today + N days]; `date_overdue`: a date before today. Both are skipped
  when the row's state is in the done set.
- `required_blank`: a required cell is empty after trimming (and its `when` condition holds).
An unparsable date is a `bad_date` note, never an event. Events reported last time and unchanged are marked
`repeat` (an overdue item keeps appearing until it is fixed)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import dates
import priority
from models import Event
from rules import Resolved
from snapshot import Keyed, Row

DONE = re.compile(r"^\s*(done|closed|complete|completed|shipped|resolved|cancelled|canceled)\s*$", re.IGNORECASE)
MAX_BAD_DATE_SAMPLES = 3


@dataclass(frozen=True)
class Result:
    events: tuple[Event, ...]
    notes: tuple[str, ...]
    bad_dates: int
    duplicates: tuple[str, ...]


def compute(keyed: Keyed, previous: Mapping[str, Row] | None, resolved: Resolved, today: date,
            reported: Sequence[str] = ()) -> Result:
    events: list[Event] = []
    bad: list[str] = []
    orders = {c: dates.column_order([r.values.get(c, "") for r in keyed.rows]) for c, _ in resolved.date_rules}
    for row in keyed.rows:
        state = row.values.get(resolved.state_column or "", "")
        done = bool(resolved.state_column) and bool(DONE.match(state))
        events.extend(_date_events(row, resolved, today, orders, done, state, bad))
        events.extend(_blank_events(row, resolved))
    if previous is not None:
        events.extend(_diff(keyed, previous, resolved))
    seen = set(reported)
    marked = [e.model_copy(update={"repeat": e.signature in seen}) for e in events]
    order = {r.key: r.order for r in keyed.rows} | {k: 10**6 + r.order for k, r in (previous or {}).items()
                                                   if k not in {x.key for x in keyed.rows}}
    notes = []
    if bad:
        samples = "; ".join(bad[:MAX_BAD_DATE_SAMPLES])
        notes.append(f"{len(bad)} date cell(s) I couldn't read as dates, so they raise no event: {samples}.")
    if keyed.duplicates:
        notes.append("duplicate_key: these keys appear more than once, so repeats were numbered (#2, #3): "
                     + ", ".join(f'"{d}"' for d in keyed.duplicates) + ".")
    return Result(tuple(priority.ranked(marked, order)), tuple(notes), len(bad), keyed.duplicates)


def _date_events(row: Row, resolved: Resolved, today: date, orders: Mapping[str, str], done: bool, state: str,
                 bad: list[str]) -> list[Event]:
    out = []
    for column, days in resolved.date_rules:
        cell = row.values.get(column, "").strip()
        if not cell:
            continue
        when = dates.parse(cell, today.year, orders.get(column, "mdy"))
        if when is None:
            bad.append(f'{column} "{cell}" ({row.label})')
            continue
        if done:
            continue
        delta = (when - today).days
        status = f', status "{state}"' if state else ""
        if delta < 0:
            out.append(Event(type="date_overdue", row_key=row.key, row_label=row.label, column=column, new=cell,
                             detail=f"was due {dates.short(when)}{status} ({-delta} day{'s' * (delta != -1)} overdue)"))
        elif delta <= days:
            soon = "today" if delta == 0 else f"in {delta} day{'s' * (delta != 1)}"
            out.append(Event(type="date_approaching", row_key=row.key, row_label=row.label, column=column, new=cell,
                             detail=f"is due {dates.short(when)} ({soon}){status}"))
    return out


def _blank_events(row: Row, resolved: Resolved) -> list[Event]:
    out = []
    for rule in resolved.required:
        if row.values.get(rule.column, "").strip():
            continue
        if rule.when and row.values.get(rule.when.column, "").strip().casefold() != rule.when.value.casefold():
            continue
        when = f" ({rule.when.column} is \"{rule.when.value}\")" if rule.when else ""
        out.append(Event(type="required_blank", row_key=row.key, row_label=row.label, column=rule.column, new="",
                         detail=f"has no {rule.column}{when}"))
    return out


def _diff(keyed: Keyed, previous: Mapping[str, Row], resolved: Resolved) -> list[Event]:
    out = []
    current = {r.key: r for r in keyed.rows}
    state_col = resolved.state_column
    for row in keyed.rows:
        before = previous.get(row.key)
        if before is None:
            if resolved.track_new_rows:
                out.append(Event(type="new_row", row_key=row.key, row_label=row.label, detail="new row"))
            continue
        old = before.values.get(state_col, "") if state_col else ""
        new = row.values.get(state_col, "") if state_col else ""
        if state_col and state_col in before.values and old.strip().casefold() != new.strip().casefold():
            out.append(Event(type="state_change", row_key=row.key, row_label=row.label, column=state_col,
                             old=old, new=new, detail=f'{state_col} "{old}" → "{new}"'))
        elif before.row_hash and before.row_hash != row.row_hash:
            moved = [c for c, v in row.values.items() if c in before.values and before.values[c] != v]
            what = f" ({', '.join(moved)} changed)" if moved else ""
            out.append(Event(type="row_changed", row_key=row.key, row_label=row.label, detail=f"row edited{what}"))
    if resolved.track_removed_rows:
        out.extend(Event(type="removed_row", row_key=key, row_label=before.label, detail="removed")
                   for key, before in previous.items() if key not in current)
    return out
