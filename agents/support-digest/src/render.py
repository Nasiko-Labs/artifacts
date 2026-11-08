"""User-facing text, rendered in code from the computed digest: headline, day-by-day counts, themes, SLA,
worst offenders, customers, backlog and notes; the text the follow-up model reads; the JSON envelope. No raw
HTML, no images; links only from tool output or the pasted input."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date

_UNSAFE = re.compile(r"[<>\[\]`*_|\\]")
_SPACES = re.compile(r"\s+")
_MAX_TEXT = 90
_KIND = {"first_reply": "first reply", "resolution": "resolution"}


def safe(text: str, most: int = _MAX_TEXT) -> str:
    """Untrusted text as one inert line: markdown and HTML characters removed, length capped."""
    flat = _SPACES.sub(" ", _UNSAFE.sub("", text or "")).strip()
    return flat if len(flat) <= most else flat[:most - 1].rstrip() + "…"


def link_ok(url: str) -> bool:
    return url.startswith(("https://", "http://")) and not re.search(r"[\s()<>\[\]`]", url)


def _day(iso: str, with_year: bool = True) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%a %Y-%m-%d") if with_year else d.strftime("%a %m-%d")


def _previous_label(days: int) -> str:
    return {1: "the previous day", 7: "the previous week"}.get(days, f"the previous {days} days")


def headline(d: dict) -> str:
    p, v = d["period"], d["volume"]
    span = p["start"] if p["start"] == p["end"] else f"{p['start']} to {p['end']}"
    days = (date.fromisoformat(p["end"]) - date.fromisoformat(p["start"])).days + 1
    head = f"Support digest, {span}: {v['total']} tickets"
    if v["previous_total"] is None:
        head += " (no previous-period count, so no trend)."
    elif v["change_pct"] == "n/a":
        head += f", none in {_previous_label(days)} to compare with ({v['previous_total']})."
    else:
        pct = abs(v["change_pct"])
        trend = (f"up {pct:.1f}% on" if v["direction"] == "up" else f"down {pct:.1f}% on"
                 if v["direction"] == "down" else f"flat ({v['change_pct']:+.1f}%) against")
        head += f", {trend} {_previous_label(days)} ({v['previous_total']})."
    if d.get("busiest"):
        head += f" Busiest day {_day(d['busiest']['date'])} ({d['busiest']['count']})."
    return head


def _by_day(v: dict) -> str:
    return "By day: " + ", ".join(f"{_day(x['date'], False)} {x['count']}" for x in v["by_day"])


def _themes(themes: Sequence[dict], min_count: int) -> str:
    if not themes:
        return f"Top themes: none with at least {min_count} tickets."
    rows = (f"{i}. {safe(t['name'], 60)} ({t['count']}, {t['share_pct']:.1f}%) \"{safe(t['example'])}\""
            for i, t in enumerate(themes, 1))
    return "Top themes:\n" + "\n".join(rows)


def _sla_line(s: dict) -> str:
    source = "desk policies" if s["source"] == "desk" else "defaults: no desk policy read"
    rate = f" ({s['first_reply_rate_pct']:.1f}%)" if s["first_reply_rate_pct"] is not None else ""
    return (f"SLA ({source}): first reply breached {s['first_reply_breaches']}{rate}, resolution breached "
            f"{s['resolution_breaches']}, open breaches {s['open_breaches']}, at risk soon {s['at_risk_soon']}, "
            f"unknown times {s['unknown_times']}.")


def breach_line(b: dict) -> str:
    ref = f"[#{safe(b['ticket_id'], 40)}]({b['url']})" if link_ok(b.get("url") or "") else f"#{safe(b['ticket_id'], 40)}"
    who = safe(b.get("customer") or "", 40) or "unknown customer"
    state = " (still waiting)" if b["open"] else ""
    return f"- {ref} {who} ({b['priority']}) {_KIND[b['kind']]} +{b['overrun_hours']:.1f} h overrun{state}"


def _customers(rows: Sequence[dict]) -> str:
    if not rows:
        return "Top customers: no organization was readable on these tickets."
    return "Top customers: " + ", ".join(f"{safe(r['name'], 40)} {r['tickets']} ({r['open']} open)" for r in rows)


def report(d: dict, notes: Sequence[str], min_count: int) -> str:
    parts = [headline(d), _by_day(d["volume"]), _themes(d["themes"], min_count), _sla_line(d["sla"])]
    if d["sla"]["worst"]:
        parts.append("Worst:\n" + "\n".join(breach_line(b) for b in d["sla"]["worst"]))
    parts.append(_customers(d["top_customers"]))
    sample = " of the sampled tickets" if d["sampled"] else ""
    parts.append(f"Backlog: {d['backlog']} still open (new, open, pending or on hold){sample}.")
    parts.extend(f"Note: {n}" for n in notes)
    return "\n\n".join(parts)


def as_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=1, ensure_ascii=False) + "\n```"


def for_followup(env: dict) -> str:
    """The stored digest as plain lines for the follow-up model (it is fenced by the caller)."""
    lines = [str(env.get("digest_markdown") or env.get("summary") or "")]
    breaches = env.get("breaches") or ()
    if breaches:
        lines.append("All SLA breaches (largest overrun first):")
        lines += [breach_line(b) for b in breaches]
    return "\n".join(lines)
