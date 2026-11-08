"""Rendering in code: the markdown table (at most 200 rows), the notes, the summary line, and the fenced
json envelope for "as json". Markdown only: no raw HTML, no images; cell text is escaped."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import models
from engine import OutColumn
from models import Pull
from spec import QuerySpec

MAX_REPLY_ROWS = 200
MAX_CELL_CHARS = 120


def as_of_text(moment: datetime) -> str:
    return f"{moment.day} {moment.strftime('%b %H:%M')} UTC"


def label(spec: QuerySpec) -> str:
    """A plain description of the query, made from the spec (never model prose)."""
    aggs = [f"{a.fn} {a.column}" for a in spec.agg]
    by = f" by {', '.join(spec.group_by)}" if spec.group_by else ""
    if spec.compare is not None:
        what = ", ".join(aggs) or "rows"
        return f"{what}{by}: {spec.compare.start.isoformat()} to {spec.compare.end.isoformat()} vs the previous period"
    if aggs:
        return f"{', '.join(aggs)}{by}"
    if spec.group_by:
        return f"rows{by}"
    return ", ".join(spec.select) if spec.select else "rows"


def summary(pull: Pull) -> str:
    src = " / ".join(p for p in (pull.source.title, pull.source.tab) if p)
    return (f"{pull.result.total_rows} row(s): {pull.label} from {src or 'the table'}, as of "
            f"{pull.as_of.strftime('%Y-%m-%dT%H:%M:%SZ')}.")


def markdown(pull: Pull) -> str:
    src = pull.source
    where = ", ".join(p for p in (src.title, f'tab "{src.tab}"' if src.tab else "") if p)
    title = _escape(pull.label[:1].upper() + pull.label[1:])
    lines = [f"**{title} ({_escape(where)}, as of {as_of_text(pull.as_of)})**"]
    result = pull.result
    if result.columns and result.rows:
        lines.append("")
        lines.extend(table_lines(result.columns, result.rows[:MAX_REPLY_ROWS]))
    lines.append("")
    shown = min(len(result.rows), MAX_REPLY_ROWS)
    count = f"{result.total_rows} row(s)" if shown == result.total_rows else \
        f"Showing {shown} of {result.total_rows} rows (ask \"as json\" for up to {models.MAX_DATA_ROWS})"
    origin = " / ".join(p for p in (src.title, src.tab, src.range) if p)
    lines.append(f"{count}. Source: {_escape(origin)}." + (f" Modified {src.modified}." if src.modified else ""))
    lines.extend(f"- {_escape(note)}" for note in pull.notes)
    return "\n".join(lines)


def table_lines(columns: Sequence[OutColumn], rows: Sequence[Sequence[Any]]) -> list[str]:
    head = "| " + " | ".join(_escape(c.name) for c in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(cell(v, c.type) for v, c in zip(r, columns, strict=True)) + " |" for r in rows]
    return [head, rule, *body]


def cell(value: Any, kind: str) -> str:
    if value is None:
        return "–"
    if kind == "percent" and isinstance(value, Decimal):
        return f"{value * 100:+.1f}%"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return _escape(str(value))


def json_block(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=1) + "\n```"


def _escape(text: str) -> str:
    clean = " ".join(str(text).split())[:MAX_CELL_CHARS]
    return clean.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
