"""Pure: the reply in markdown, built by code from computed numbers and verbatim quotes. No images or raw
HTML; a link appears only if the customer's own text holds one."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from caveats import plural
from items import FeedbackItem
from metrics import Dataset, ThemeStats

ITEMS_LISTED = 50
CUSTOMERS_LISTED = 6
FOLLOW_UPS = ('_Ask: "only churn signals", "top 3", "enterprise only", "sort by customers", '
              '"show quotes for theme 1", "show items for theme 2", or paste more rows with "add these too"._')
_TAG = re.compile(r"<[^>\n]{1,200}>")
_IMAGE = re.compile(r"!\[")


def clean(text: str) -> str:
    """Customer text, safe to render: no HTML tags, no images, one line."""
    return " ".join(_IMAGE.sub("[", _TAG.sub("", text or "")).split())


def cell(text: str) -> str:
    return clean(text).replace("|", "/")


def money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "ARR unknown"


def markdown(shown: Sequence[ThemeStats], quoted: Mapping[str, list[dict]], signals: Sequence[dict],
             notes: Sequence[str], data: Dataset, view, by_id: Mapping[str, FeedbackItem]) -> str:
    header = (f"**Feedback themes** ({plural(data.items, 'item')}, {plural(data.customers, 'customer')}; "
              f"{plural(len(shown), 'theme')} shown)")
    if view.only_churn:
        return "\n\n".join((header, churn_section(signals, empty_line=True)))
    rows = list(shown[:view.top] if view.top else shown)
    if view.items_for:
        return "\n\n".join((header, items_section(rows, view.items_for, by_id)))
    if view.quotes not in ("", "all"):
        return "\n\n".join((header, quote_section(rows, quoted, int(view.quotes))))
    blocks = [header, table(rows) if rows else "No theme reached three items or two customers."]
    blocks += [details(n, s, quoted.get(s.id, []), every=view.quotes == "all") for n, s in enumerate(rows, start=1)]
    blocks += [churn_section(signals), caveat_section(notes), FOLLOW_UPS]
    return "\n\n".join(b for b in blocks if b)


def table(rows: Sequence[ThemeStats]) -> str:
    lines = ["| # | Theme | Type | Items | Customers | Weight | Trend |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {n} | {cell(s.name)} | {s.type} | {s.count} | {s.distinct_customers} | {s.weighted_score:.1f}% | "
              f"{s.trend} |" for n, s in enumerate(rows, start=1)]
    return "\n".join(lines)


def details(n: int, stat: ThemeStats, quotes: Sequence[dict], every: bool) -> str:
    lines = [f"**{n}. {clean(stat.name)}**"]
    lines += [_quote_line(q) for q in (quotes if every else quotes[:1])]
    names = [clean(c) for c in stat.customers[:CUSTOMERS_LISTED]]
    if names:
        more = f" (+{stat.distinct_customers - CUSTOMERS_LISTED} more)" if stat.distinct_customers > CUSTOMERS_LISTED \
            else ""
        lines.append(f"Customers: {', '.join(names)}{more}")
    lines.append(f"Next step: {stat.next_step}")
    return "\n".join(lines)


def quote_section(rows: Sequence[ThemeStats], quoted: Mapping[str, list[dict]], number: int) -> str:
    if not 1 <= number <= len(rows):
        return f"There is no theme {number}; the table has {len(rows)}."
    stat = rows[number - 1]
    lines = [f"**Quotes for theme {number}: {clean(stat.name)}**"]
    return "\n".join(lines + [_quote_line(q) for q in quoted.get(stat.id, [])])


def items_section(rows: Sequence[ThemeStats], number: int, by_id: Mapping[str, FeedbackItem]) -> str:
    if not 1 <= number <= len(rows):
        return f"There is no theme {number}; the table has {len(rows)}."
    stat = rows[number - 1]
    lines = [f"**Items in theme {number}: {clean(stat.name)}** ({stat.count})"]
    for item_id in stat.item_ids[:ITEMS_LISTED]:
        item = by_id[item_id]
        meta = ", ".join(x for x in (clean(item.customer) or "unknown", item.date) if x)
        lines.append(f"- {clean(item.text)} ({meta})")
    if stat.count > ITEMS_LISTED:
        lines.append(f"_(and {stat.count - ITEMS_LISTED} more)_")
    return "\n".join(lines)


def churn_section(signals: Sequence[dict], empty_line: bool = False) -> str:
    if not signals:
        return "**Churn signals** (0)\nNone found." if empty_line else ""
    lines = [f"**Churn signals** ({len(signals)})"]
    for s in signals:
        when = f", {s['date']}" if s["date"] else ""
        arr = f" (ARR {money(s['arr'])}{when})" if s["arr"] is not None else (f" ({s['date']})" if s["date"] else "")
        lines.append(f"- {clean(s['customer'])} ({s['kind'].replace('_', ' ')}): \"{clean(s['quote'])}\"{arr}")
    return "\n".join(lines)


def caveat_section(notes: Sequence[str]) -> str:
    return "**Caveats**\n" + "\n".join(f"- {n}" for n in notes) if notes else ""


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def _quote_line(quote: Mapping[str, str]) -> str:
    meta = ", ".join(x for x in (clean(quote.get("customer") or "unknown"), quote.get("date") or "") if x)
    return f"> \"{clean(quote['text'])}\" ({meta})"
