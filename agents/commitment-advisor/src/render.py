"""Pure: the markdown reply (no HTML, no images, no links) and the JSON mode text, rendered from the result."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import fmt

ACTION_TEXT = {"renew_more": "renew more", "renew_same": "renew at the same level", "renew_less": "renew less",
               "let_lapse": "let it lapse", "underused": "underused", "keep": "keep"}
FOOTER = "_Every number is computed in code; I recommend only and purchase nothing._"


def _header(result: Mapping[str, Any]) -> str:
    c, d = result["coverage"], result["discounts"]
    safety = ", safety 0.90" if c["granularity"] == "daily" else ""
    kind = "quoted" if result["quotes"] else "assumed"
    return (f"**Commitment review** ({kind} discounts: 1y {fmt.pct(d['1'])}, 3y {fmt.pct(d['3'])}; min utilization "
            f"{fmt.pct(result['min_utilization'])}; {c['days']:g} days of {c['granularity']} data{safety})")


def _alternative(s: Mapping[str, Any]) -> str:
    if not s.get("eligible"):
        return f"  - {s['term_years']}y: no eligible level."
    return (f"  - {s['term_years']}y: {fmt.money(s['hourly_commit'])} $/hr, saves {fmt.money(s['monthly_saving'])}"
            f"/month ({fmt.money(s['term_saving'])} over the term), utilization {fmt.pct(s['utilization'])}, "
            f"downside {fmt.money(s['downside_monthly_saving'])}/month, risk {s['risk']}")


def _rec(r: Mapping[str, Any]) -> list[str]:
    head = f"**Scope {r['scope']}:** "
    if r["action"] == "needs_more_data":
        return [head + "not enough data (at least 14 days needed)."]
    if r["action"] == "no_commitment":
        return [head + f"no commitment recommended ({r['reason']}). Confidence {r['confidence']}.",
                *[_alternative(s) for s in r["terms"].values()]]
    lines = [head + (f"buy a {r['term_years']}y plan at {fmt.money(r['hourly_commit'])} $/hr -> saves about "
                     f"{fmt.money(r['monthly_saving'])}/month ({fmt.money(r['term_saving'])} over the term), "
                     f"utilization {fmt.pct(r['utilization'])}, break-even {fmt.pct(r['break_even_utilization'])}, "
                     f"coverage {fmt.pct(r['coverage_pct'])}, risk {r['risk']}, confidence {r['confidence']}"),
             (f"- Downside (-20% usage): saves {fmt.money(r['downside_monthly_saving'])}/month at "
              f"{fmt.pct(r['downside_utilization'])} utilization."), "- Both terms:"]
    return lines + [_alternative(s) for s in r["terms"].values()]


def existing_line(e: Mapping[str, Any]) -> str:
    when = (f"ends in {e['ends_in_days']} days" if e["ends_in_days"] >= 0 else
            f"ended {-e['ends_in_days']} days ago")
    text = f"- {e['id']} ({e['type']}, {fmt.money(e['hourly_commit'])} $/hr, {when}): {ACTION_TEXT[e['action']]}"
    if e.get("target_hourly_commit") is not None and e["action"] in ("renew_more", "renew_less"):
        text += f" to {fmt.money(e['target_hourly_commit'])} $/hr"
    if e.get("portfolio_utilization") is not None:
        text += f" (portfolio utilization {fmt.pct(e['portfolio_utilization'])})"
    if e.get("wasted_per_month") is not None:
        text += f", wasting about {fmt.money(e['wasted_per_month'])}/month"
    return text


def render(result: Mapping[str, Any], notes: tuple[str, ...] = ()) -> str:
    out = [_header(result), ""]
    for r in result["recommendations"]:
        out += _rec(r)
    if result["existing"]:
        out += ["", "**Existing commitments**", *[existing_line(e) for e in result["existing"]]]
    ex = result.get("explanation")
    if ex:
        out += ["", f"**Summary:** {ex['summary']}", f"**Why:** {ex['why']}", f"**Main risk:** {ex['risk']}"]
    c = result["coverage"]
    out += ["", f"**Coverage:** {c['rows_used']:,} of {c['rows_read']:,} usage rows used ({c['rows_dropped']:,} "
                f"dropped), {c['start']} to {c['end']}, {c['gap_hours']:,} gap hours filled with 0"
                + (", span trimmed to the longest continuous stretch" if c["trimmed"] else "") + "."]
    out += [f"- {n}" for n in result.get("notes") or []]
    out += ["", "**Assumptions**", *[f"- {a}" for a in result["assumptions"]]]
    out += [f"_{n}_" for n in notes]
    out += ["", FOOTER]
    return "\n".join(out)


def as_json(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, indent=1, ensure_ascii=False) + "\n```"
