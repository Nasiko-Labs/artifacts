"""Pure: the reply, rendered from the checked ledger only. Re-render views cost no LLM call."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import grounding as g
import prompts
from envelope import Stats
from ledger import Ledger, Row
from promises import NOT_STATED

QUOTE_MAX_CHARS = 250


@dataclass(frozen=True)
class View:
    overdue_only: bool = False
    to: str = ""
    sources: bool = False


DEFAULT_VIEW = View()


def render(ledger: Ledger, stats: Stats, view: View = DEFAULT_VIEW, notes: Sequence[str] = ()) -> str:
    rows = _filtered(ledger, view)
    if not ledger.rows:
        body = prompts.NONE_FOUND
    elif not rows:
        body = _empty_view(view)
    else:
        title = "**Your overdue promises**" if view.overdue_only else "**Your promises**"
        title += f" (as of {ledger.as_of.isoformat()}, read from {stats.messages} {_plural(stats.messages, 'message')})"
        parts = [title, _table(rows)]
        if view.sources:
            parts.append(_sources(rows))
        if ledger.overdue and not view.overdue_only:
            parts.append(_overdue_line(ledger.overdue))
        body = "\n".join(parts[:2]) + "".join(f"\n\n{p}" for p in parts[2:])
    return "\n\n".join(p for p in (body, footer(ledger, stats), *notes) if p)


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def footer(ledger: Ledger, stats: Stats) -> str:
    lines = []
    if stats.dropped:
        lines.append(f"{stats.dropped} {_plural(stats.dropped, 'candidate')} left out: quote not found in your text.")
    if ledger.capped:
        lines.append(f"{ledger.capped} more left out to keep the list at 15.")
    if stats.speaker_guess:
        lines.append(f"Read {stats.speaker_guess}'s messages as yours.")
    if stats.failed_parts or stats.timed_out_parts:
        missed = stats.failed_parts + stats.timed_out_parts
        lines.append(f"Covered {stats.parts - missed} of {stats.parts} parts of your text; the rest couldn't be read.")
    if any(r.promise.basis == "clock" for r in ledger.rows):
        lines.append("Some messages had no date, so relative deadlines were counted from today.")
    return f"_({' '.join(lines)})_" if lines else ""


def _filtered(ledger: Ledger, view: View) -> tuple[Row, ...]:
    rows = ledger.overdue if view.overdue_only else ledger.rows
    if view.to:
        rows = tuple(r for r in rows if g.contains(view.to, g.normalise(r.promise.to_whom)))
    return rows


def _empty_view(view: View) -> str:
    if view.to:
        return f"None of your promises are to {view.to}."
    return "None of your promises are overdue."


def _table(rows: Sequence[Row]) -> str:
    lines = ["| # | To | What | Due | Overdue | State |", "|---|---|---|---|---|---|"]
    for row in rows:
        p = row.promise
        overdue = f"{row.days_overdue} {_plural(row.days_overdue, 'day')}" if row.days_overdue else ""
        lines.append(f"| {row.number} | {_cell(p.to_whom or NOT_STATED)} | {_cell(p.what)} | {p.due or 'no date'} | "
                     f"{overdue} | {p.state} |")
    return "\n".join(lines)


def _sources(rows: Sequence[Row]) -> str:
    lines = ["**Sources**"]
    for row in rows:
        lines.append(f'{row.number}. > "{_quote(row.promise.source_quote)}"')
        if row.promise.evidence_quote:
            lines.append(f'   done: > "{_quote(row.promise.evidence_quote)}"')
    return "\n".join(lines)


def _overdue_line(rows: Sequence[Row]) -> str:
    listed = ", ".join(f"{_cell(r.promise.what)}, {r.days_overdue} {_plural(r.days_overdue, 'day')}" for r in rows)
    return f"**Overdue:** {len(rows)} ({listed})"


def _cell(text: str) -> str:
    return " ".join(str(text).replace("|", "/").replace("<", "‹").replace(">", "›").split())


def _quote(text: str) -> str:
    cut = text if len(text) <= QUOTE_MAX_CHARS else text[:QUOTE_MAX_CHARS].rstrip() + "…"
    return _cell(cut)


def _plural(count: int | None, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"
