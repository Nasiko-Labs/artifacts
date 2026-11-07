"""Pure: the markdown reply, rendered by code from the envelope (no raw HTML, no images; URLs only from tool
output, already checked). Also the fenced JSON form for workflow steps and the copyable issue draft."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from arr import money
from caveats import plural

_HTML = str.maketrans({"<": "‹", ">": "›"})
_MD_LINK = re.compile(r"[\[\]]")
MAX_CHURN_SHOWN = 8


def clean(text: object) -> str:
    """Untrusted text made inert for markdown: no tags, no link syntax, one line."""
    return _MD_LINK.sub("", " ".join(str(text or "").split()).translate(_HTML))


def link(label: str, url: str) -> str:
    return f"[{clean(label)}]({url})" if url else clean(label)


def report(env: Mapping) -> str:
    themes = env.get("themes") or []
    window = env.get("window") or {}
    sources = env.get("sources") or {}
    read = ", ".join(f"{name} {count}" for name, count in sources.items()) or "no source"
    total = sum(int(c) for c in sources.values())
    lines = [(f"**Feedback, {window.get('start', '?')} to {window.get('end', '?')}:** {plural(total, 'item')} ({read}), "
              f"{plural(len(themes), 'theme')}, {plural(len(env.get('churn_signals') or []), 'churn signal')}."), ""]
    if not themes:
        lines += ["No theme reached three items or two customers.", ""]
    for n, theme in enumerate(themes, start=1):
        lines += _theme(n, theme)
    lines += _churn(env.get("churn_signals") or [])
    caveat_lines = env.get("caveats") or []
    if caveat_lines:
        lines += ["**Caveats**", *(f"- {clean(c)}" for c in caveat_lines), ""]
    lines.append(f"Confidence: {env.get('confidence')}. {'A person should review this.' if env.get('needs_human') else ''}"
                 .rstrip())
    return "\n".join(lines).strip()


def _theme(n: int, theme: Mapping) -> list[str]:
    trend = theme.get("trend") or "unknown"
    lines = [(f"{n}. **{clean(theme['name'])}** ({theme['type']}): {plural(theme['count'], 'item')}, "
              f"{plural(theme['distinct_customers'], 'customer')}, weight {theme['weighted_score']:.1f}%, {trend}")]
    for quote in [q for q in theme.get("quotes") or () if isinstance(q, Mapping)]:
        who = ", ".join(p for p in (clean(quote.get("customer")), quote.get("date") or "") if p)
        source = f" {link('source', quote['url'])}" if quote.get("url") else ""
        lines.append(f'   - "{clean(quote.get("text"))}" ({who}){source}')
    links = [i for i in theme.get("linked_issues") or [] if isinstance(i, Mapping)]
    if links:
        shown = " | ".join(f"{link(str(i.get('id', '')), i.get('url', ''))} ({clean(i.get('state'))}"
                           f"{', check it solved this' if i.get('closed') else ''})" for i in links)
        lines.append(f"   - Linked: {shown}")
    lines.append(f"   - Next step: {theme['suggested_next_step']}")
    lines.append(f"   - Issue: {clean(theme.get('link_action', ''))}")
    return [*lines, ""]


def _churn(signals: Sequence[Mapping]) -> list[str]:
    if not signals:
        return []
    lines = ["**Churn signals**"]
    for s in signals[:MAX_CHURN_SHOWN]:
        arr = f", ARR {money(s.get('arr'))}" if s.get("arr") is not None else ""
        source = f" {link('source', s['url'])}" if s.get("url") else ""
        lines.append(f"- {clean(s['customer'])} ({str(s['kind']).replace('_', ' ')}{arr}, {s.get('date') or 'undated'}): "
                     f"\"{clean(s['quote'])}\"{source}")
    if len(signals) > MAX_CHURN_SHOWN:
        lines.append(f"- and {len(signals) - MAX_CHURN_SHOWN} more in the JSON result")
    return [*lines, ""]


def as_json(env: Mapping) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def issue_draft(n: int, theme: Mapping) -> str:
    """Copyable text only: nothing is created in the tracker."""
    body = [(f"Customer feedback theme ({theme['type']}): {plural(theme['count'], 'item')} from "
             f"{plural(theme['distinct_customers'], 'customer')}, weighted share {theme['weighted_score']:.1f}%, "
             f"trend {theme.get('trend') or 'unknown'}."), "", "Customers: " + (", ".join(theme["customers"]) or "unknown"),
            "", "What customers said:"]
    body += [f'- "{q["text"]}" ({q.get("customer") or "unknown"}{", " + q["date"] if q.get("date") else ""})'
             for q in theme.get("quotes") or ()]
    body += ["", f"Suggested next step: {theme['suggested_next_step']}"]
    text = "\n".join(body).replace("```", "'''")
    return (f"Draft issue for theme {n} (copy it into your tracker; I don't create issues):\n\n"
            f"**Title:** {clean(theme['name'])}\n\n```\n{text}\n```")
