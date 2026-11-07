"""Pure: the reply in fixed sections, from code-computed values and checked model text only. Views re-render
the stored result with zero LLM calls."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import envelope
from digest import money, pct

EMPTY = "None found."
VIEWS = ("default", "movers", "anomalies", "untagged", "waste", "recommendations", "chart", "sources")
HYPOTHESIS_NOTE = "_(Hypotheses are inferred from the columns in your export, not confirmed causes.)_"


def render(result: Mapping[str, Any], view: str = "default", extra_notes: Sequence[str] = ()) -> str:
    cur = result["totals"].get("currency")
    sections = {
        "movers": lambda: _movers(result, cur, sources=False),
        "sources": lambda: _movers(result, cur, sources=True),
        "anomalies": lambda: _anomalies(result, cur),
        "untagged": lambda: _untagged(result, cur),
        "waste": lambda: _waste(result, cur),
        "recommendations": lambda: _recs(result, cur),
        "chart": lambda: "**Chart**\n\n" + result["chart"],
    }
    if view in sections:
        body = [_headline(result, cur), sections[view]()]
    else:
        body = [_headline(result, cur), "**Narrative** " + (result["narrative"] or EMPTY), _movers(result, cur),
                _anomalies(result, cur), _untagged(result, cur), _waste(result, cur), _recs(result, cur),
                "**Chart**\n\n" + result["chart"]]
    return "\n\n".join(p for p in (*body, footer(result, extra_notes)) if p)


def as_json(envelope_data: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope_data, ensure_ascii=False, indent=1, default=str) + "\n```"


def _headline(r: Mapping[str, Any], cur: str | None) -> str:
    t, p = r["totals"], r["period"]
    line = (f"**Period {p['start']} to {p['end']} ({p['days']} days).** Gross {money(t['gross'], cur)} · credits "
            f"{money(t['credits'], cur)} · net {money(t['net'], cur)} · {money(t['daily_avg'], cur)}/day")
    b = t.get("budget")
    if b:
        line += (f"\n**Budget {b['month']}:** {money(b['amount'], cur)}; spent {money(b['spent_to_date'], cur)} in "
                 f"{b['days_elapsed']} days, projected {money(b['projected'], cur)} "
                 f"({pct(b['projected_vs_budget_pct'])} vs budget; {b['label']}).")
    return line


def _movers(r: Mapping[str, Any], cur: str | None, sources: bool = False) -> str:
    label = r["window"]["label"]
    if not r["window"]["comparable"]:
        return "**Top movers** " + (r["window"]["note"] or "No comparison possible.")
    if not r["top_movers"]:
        return f"**Top movers ({label})** {EMPTY}"
    rows = [f"| {m['name']} | {money(m['prev'], cur)} | {money(m['cur'], cur)} | {money(m['change_abs'], cur, True)} "
            f"| {_pct(m)} | {m['likely_cause'] or _drivers_text(m, cur)} |" for m in r["top_movers"]]
    text = (f"**Top movers ({label})**\n| Service | Previous | Current | Change | % | Hypothesis / drivers |\n"
            "|---|---|---|---|---|---|\n" + "\n".join(rows))
    if sources:
        lines = [f"- `{d['id']}` {m['name']}: {d['dimension'].replace('tag:', 'tag ')} = {d['value']} "
                 f"{money(d['delta'], cur, True)}" + (f" ({d['share_pct']:.0f}% of the change)"
                                                      if d["share_pct"] is not None else "")
                 for m in r["top_movers"] for d in m["evidence"]]
        text += "\n\n**Evidence**\n" + ("\n".join(lines) or EMPTY)
    return text


def _pct(m: Mapping[str, Any]) -> str:
    return m["status"] or pct(m["change_pct"])


def _drivers_text(m: Mapping[str, Any], cur: str | None) -> str:
    top = m["evidence"][:1]
    return "; ".join(f"driver: {d['value']} {money(d['delta'], cur, True)}" for d in top) or "-"


def _anomalies(r: Mapping[str, Any], cur: str | None) -> str:
    if r["period"]["granularity"] == "monthly":
        return "**Anomalies** Anomalies need daily data."
    lines = [f"- {a['date_start']}" + (f" to {a['date_end']}" if a["date_end"] != a["date_start"] else "")
             + f" {a['service']}: {money(a['value'], cur)} vs trailing mean {money(a['mean'], cur)} "
             f"({a['sigma']:+.1f}σ, {a['direction']})" for a in r["anomalies"]]
    return "**Anomalies**\n" + ("\n".join(lines) or EMPTY)


def _untagged(r: Mapping[str, Any], cur: str | None) -> str:
    u = r.get("untagged")
    if not u:
        return "**Untagged** Untagged share can't be computed: no tag column in this export."
    top = ", ".join(f"{s['service']} {money(s['cost'], cur)}" for s in u["top_services"][:3])
    return (f"**Untagged ({u['tag']}):** {pct(u['share_pct'], signed=False)} of gross spend, {money(u['cost'], cur)}"
            + (f" (top: {top})" if top else ""))


def _waste(r: Mapping[str, Any], cur: str | None) -> str:
    lines = [f"- **{w['id']} {w['title']}**: {w['text']} " + (f"Estimate: ~{money(w['est_monthly'], cur)}/month "
             f"({w['assumption']})." if w["est_monthly"] else f"({w['assumption']}.)") for w in r["waste_patterns"]]
    return "**Waste patterns**\n" + ("\n".join(lines) or EMPTY)


def _recs(r: Mapping[str, Any], cur: str | None) -> str:
    lines = [f"- {x['text']}" + (f" Estimate ~{money(x['est_monthly'], cur)}/month." if x["est_monthly"] else "")
             + f" _Assumes: {x['assumption']}._ [{', '.join(x['evidence_ids'])}]" for x in r["recommendations"]]
    return "**Recommendations**\n" + ("\n".join(lines) or EMPTY)


def footer(r: Mapping[str, Any], extra_notes: Sequence[str] = ()) -> str:
    level = envelope.confidence(r)
    meta = [f"Read {r['rows_read']:,} rows", f"confidence {level}"]
    if r["dropped"]:
        meta.append(f"{r['dropped']} model item{'s' if r['dropped'] != 1 else ''} dropped (not backed by the numbers)")
    if r["narrative_source"] != "model":
        meta.append("narrative written by code")
    lines = [HYPOTHESIS_NOTE if any(m["likely_cause"] for m in r["top_movers"]) else "", f"_({'; '.join(meta)}.)_"]
    lines += [f"_{n}_" for n in (*r["notes"], *r["not_computed"]) if n]
    lines += [n for n in extra_notes if n]
    return "\n".join(x for x in lines if x)
