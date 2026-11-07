"""Pure: the reply in fixed sections, from code-computed values and checked model text only. Markdown, no raw HTML,
no images. Views re-render the stored result with zero LLM calls."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import envelope
import fmt
from digest import estimate_text

EMPTY = "None found."
VIEWS = ("default", "model", "team", "task", "waste", "recommendations")
ESTIMATE_NOTE = "_(Estimates are potential and not additive.)_"
BASIS = {"reported": "cost reported by the export", "computed": "cost computed from the built-in price table",
         "unknown": "cost unknown"}


def render(result: Mapping[str, Any], view: str = "default", extra_notes: Sequence[str] = ()) -> str:
    tl = result["team_label"]
    sections = {"model": lambda: _table(result, "by_model", "Model"),
                "team": lambda: _table(result, "by_team", tl.capitalize()),
                "task": lambda: _table(result, "by_task", "Task"),
                "waste": lambda: _waste(result), "recommendations": lambda: _recs(result)}
    if view in sections:
        body = [_headline(result), sections[view]()]
    else:
        body = [_headline(result), "**Summary** " + (result["summary"] or EMPTY),
                _table(result, "by_model", "Model"), _table(result, "by_team", tl.capitalize()),
                _table(result, "by_task", "Task"), _cross(result), _change(result), _waste(result), _recs(result)]
    return "\n\n".join(p for p in (*body, footer(result, extra_notes)) if p)


def as_json(data: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n```"


def _headline(r: Mapping[str, Any]) -> str:
    t, p = r["totals"], r["period"]
    span = f"Period {p['start']} to {p['end']} ({p['days']} days)" if p else "No dates in this export"
    tokens = fmt.count(t["input_tokens"] + t["output_tokens"])
    mix = t["cost_basis_mix"]
    basis = "; ".join(f"{BASIS[k]}: {v:,} rows" for k, v in sorted(mix.items())) if len(mix) > 1 else \
        BASIS.get(next(iter(mix), "reported"), "")
    return (f"**{span} · {fmt.money(t['cost'])} · {tokens} tokens · {fmt.count(t['requests'])} requests** "
            f"({basis}; cached input {fmt.pct(t['cached_share_pct'])})")


def _table(r: Mapping[str, Any], name: str, label: str) -> str:
    rows = r[name]
    if not rows:
        return f"**By {label.lower()}** Not in this export."
    lines = [f"| {fmt.safe(x['key'])} | {fmt.money(x['cost'])} | {fmt.pct(x['share_pct'])} | {fmt.count(x['requests'])} | "
             f"{fmt.count(x['avg_input_tokens'])} / {fmt.count(x['avg_output_tokens'])} | "
             f"{fmt.pct(x['cached_share_pct'])} | {fmt.money(x['cost_per_1k_output'])} |" for x in rows]
    return (f"**By {label.lower()}**\n| {label} | Cost | Share | Requests | Avg in / out tokens | Cached | "
            "Cost per 1k output |\n|---|---|---|---|---|---|---|\n" + "\n".join(lines))


def _cross(r: Mapping[str, Any]) -> str:
    if not r["cross"]:
        return ""
    lines = [f"- {fmt.safe(c['team'])} × {fmt.safe(c['model'])}: {fmt.money(c['cost'])} ({fmt.pct(c['share_pct'])})" for c in r["cross"]]
    return "**Top team × model**\n" + "\n".join(lines)


def _change(r: Mapping[str, Any]) -> str:
    c = r["change"]
    if not c:
        return ""
    lines = [f"- {fmt.safe(x['key'])}: {fmt.money(x['prev'])} → {fmt.money(x['cur'])} ({fmt.money(x['change'], True)}, "
             f"{fmt.pct(x['change_pct'], True) if x['change_pct'] is not None else 'new'})" for x in c["rows"]]
    return f"**Change ({c['label']})**\n" + ("\n".join(lines) or EMPTY)


def _waste(r: Mapping[str, Any]) -> str:
    lines = [f"{i}. **{f['rule_id']} {f['title']}** ({f['id']}): {fmt.safe(f['evidence'])}. Estimate: {estimate_text(f)} "
             f"({f['assumption']})." for i, f in enumerate(r["waste"], start=1)]
    notes = [f"- {n}" for n in r["not_checked"]]
    return "**Waste**\n" + ("\n".join(lines) or EMPTY) + ("\n" + "\n".join(notes) if notes else "")


def _recs(r: Mapping[str, Any]) -> str:
    lines = [f"- {fmt.safe(x['text'])} _Assumes: {_assumption(x['assumption'])}._ [{x['finding_id']}]" for x in r["recommendations"]]
    tail = f"\n{ESTIMATE_NOTE}" if any(f["est_in_export"] is not None for f in r["waste"]) else ""
    return "**Recommendations**\n" + ("\n".join(lines) or EMPTY) + tail


def _assumption(text: str) -> str:
    return re.sub(r"^\s*assumes\s+", "", fmt.safe(text), flags=re.IGNORECASE)


def footer(r: Mapping[str, Any], extra_notes: Sequence[str] = ()) -> str:
    level = envelope.confidence(r)
    meta = [f"read {r['rows_read']:,} rows", f"confidence {level}"]
    if r["rows_dropped"]:
        reasons = ", ".join(f"{v:,} {k}" for k, v in r["dropped"].items())
        meta.append(f"{r['rows_dropped']:,} rows skipped ({reasons})")
    if r["recs_dropped"]:
        meta.append(f"{r['recs_dropped']} model item{'s' if r['recs_dropped'] != 1 else ''} dropped "
                    "(not backed by the numbers)")
    if r["rec_source"] != "model":
        meta.append("summary and recommendations written by code")
    if r["price_as_of"]:
        meta.append(f"prices as of {r['price_as_of']}")
    lines = [f"_({'; '.join(meta)}.)_"] + [f"_{n}_" for n in r["not_computed"]]
    if envelope.needs_human(r, level):
        lines.append("_Needs a human look before acting._")
    return "\n".join([*lines, *(n for n in extra_notes if n)])
