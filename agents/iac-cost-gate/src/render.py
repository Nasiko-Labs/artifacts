"""Pure: the markdown reply from the checked result, in fixed sections; or the envelope as a fenced JSON
block. Re-render views cost no model call. No raw HTML, no images; links never added."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import envelope
import prompts
from verdict import money

NONE_FOUND = "None found."
VIEWS = ("default", "violations", "high", "lines", "assumptions")


def as_json(data: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def header(r: Mapping[str, Any]) -> str:
    c = r["counts"]
    head = f"**Verdict: {r['verdict'].upper()}** · estimated **{money(r['estimated_monthly'])}/month**"
    if r["limit"] is not None:
        head += f" (limit {money(r['limit'])})"
    if r["delta"] is not None:
        head += f" · before {money(r['estimated_monthly_before'])}, change **{money(r['delta'], signed=True)}/month**"
    head += f" · {_plural(c['unpriced'], 'resource')} unpriced, {c['usage_dependent']} usage-dependent"
    why = f"\nWhy: {'; '.join(r['verdict_reasons'])}." if r["verdict_reasons"] else ""
    return head + why


def lines_table(r: Mapping[str, Any], limit: int | None = envelope.TOP_LINES) -> str:
    rows = r["lines"] if limit is None else r["lines"][:limit]
    if not rows:
        return "**Cost lines**\n" + NONE_FOUND
    plan = r["delta"] is not None
    head = "| Resource | Monthly | Basis |" + (" Change |" if plan else "")
    sep = "|---|---|---|" + ("---|" if plan else "")
    body = []
    for ln in rows:
        name = ln["address"] + (f" ×{ln['qty']}" if ln["qty"] != 1 else "") + (f" ({ln['detail']})" if ln["detail"] else "")
        cell = money(ln["monthly"]) if ln["basis"] == "priced" else "-"
        body.append(f"| {name} | {cell} | {ln['basis'].replace('_', '-')} |" + (f" {ln['change']} |" if plan else ""))
    more = len(r["lines"]) - len(rows)
    tail = f"\n_+{more} more lines; say \"cost table\" for all._" if more > 0 else ""
    return "**Cost lines**\n" + "\n".join([head, sep, *body]) + tail


def violations_section(r: Mapping[str, Any], only_medium: bool = False) -> str:
    found = [v for v in envelope.violations(r) if not only_medium or v["severity"] == "medium"]
    title = "**Violations** (medium only)" if only_medium else "**Violations** (savings are potential, not additive)"
    if not found:
        return f"{title}\n{NONE_FOUND}"
    out = []
    for n, v in enumerate(found, start=1):
        saving = f": ~ {money(v['est_monthly_saving'])}/month saving" if v["est_monthly_saving"] else ""
        why = f" ({v['assumption']})" if v["assumption"] else ""
        out.append(f"{n}. `{v['rule_id']}` {v['address']}: {v['title']} [`{v['attribute']}` = `{v['evidence']}`, "
                   f"{v['severity']}]{saving}{why}. Fix: {v['fix']}")
    return f"{title}\n" + "\n".join(out)


def assumptions_section(r: Mapping[str, Any]) -> str:
    items = [f"- {a}" for a in r["assumptions"]]
    if r["unpriced"]:
        items.append("- Unpriced: " + "; ".join(f"{u['address']} ({u['reason']})" for u in r["unpriced"]))
    if r["usage_dependent"]:
        items.append("- Usage-dependent ($0 shown, not estimated): " + ", ".join(r["usage_dependent"]))
    return "**Assumptions**\n" + ("\n".join(items) or NONE_FOUND)


def footer(r: Mapping[str, Any], notes: Sequence[str] = ()) -> str:
    parts = [prompts.DISCLAIMER.format(as_of=r["as_of"])]
    if r.get("dropped"):
        parts.append(f"_({_plural(r['dropped'], 'item')} left out because a check failed.)_")
    return "\n".join([*parts, *r["notes"], *notes])


def render(r: Mapping[str, Any], view: str = "default", notes: Sequence[str] = ()) -> str:
    if view == "violations":
        body = [header(r), violations_section(r)]
    elif view == "high":
        body = [header(r), violations_section(r, only_medium=True)]
    elif view == "lines":
        body = [header(r), lines_table(r, None)]
    elif view == "assumptions":
        body = [header(r), assumptions_section(r)]
    else:
        body = [header(r), r["narrative"], lines_table(r), violations_section(r), assumptions_section(r)]
    return "\n\n".join(p for p in (*body, footer(r, notes)) if p)
