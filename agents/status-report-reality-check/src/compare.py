"""Pure: matched report items x tickets -> the envelope. Runs on turn 1 and again, with zero model calls,
whenever a follow-up changes a threshold, the as-of date or the status map."""

from __future__ import annotations

from datetime import date

import caveats as caveats_mod
import envelope
import evidence
import rules
import statuses as st
from dates import from_iso
from match import NONE
from options import Options

MAX_UNREPORTED = 10


def run(items: list[dict], tickets: list[dict], opts: Options, today: date, meta: dict) -> dict:
    as_of = from_iso(opts.as_of) or today
    ctx = rules.Ctx(as_of, opts.stale_days, opts.soon_days, opts.mapping())
    by_id = {t["id"]: t for t in tickets}
    children = _children(tickets, ctx)
    found, covered = [], set()
    for item in items:
        units = [(by_id[i], children.get(i, [])) for i in item["match"]["ids"] if i in by_id]
        covered |= {x["id"] for t, kids in units for x in (t, *kids)}
        if item["reported"] == st.UNKNOWN or not units or (opts.only_epics and not any(k for _, k in units)):
            continue
        disagreement = _check(item, units, ctx)
        if disagreement:
            found.append(disagreement)
    found.sort(key=lambda d: (rules.RISK_ORDER[d["risk"]], not d["headline"], d["order"]))
    unmatched = [{"item": i["item"], "reported": i["reported_words"] or st.label(i["reported"])}
                 for i in items if i["match"]["kind"] == NONE]
    result = {"as_of": as_of.isoformat(), "stale_days": opts.stale_days, "soon_days": opts.soon_days,
              "disagreements": found, "unmatched": unmatched, "report_items": len(items), "tickets": len(tickets)}
    if opts.unreported:
        result["unreported_risks"] = _unreported(tickets, covered, ctx)
    notes = caveats_mod.build(items, tickets, ctx, meta)
    return envelope.build(result, items, tickets, ctx, meta, notes)


def _children(tickets: list[dict], ctx: rules.Ctx) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in tickets:
        if t.get("parent_id") and t["parent_id"] != t["id"] and rules.category(t, ctx) != st.CANCELLED:
            out.setdefault(t["parent_id"], []).append(t)
    return out


def _check(item: dict, units: list[tuple[dict, list[dict]]], ctx: rules.Ctx) -> dict | None:
    fired_all: list[str] = []
    lines: list[str] = []
    ids: list[str] = []
    for ticket, kids in units:
        f = rules.facts(ticket, kids, ctx)
        fired = rules.fired(item["reported"], item.get("owner"), f, ctx)
        if fired:
            fired_all += [r for r in fired if r not in fired_all]
            lines.append(evidence.epic(ticket, f, ctx.as_of) if f.is_epic else evidence.ticket(ticket, f, ctx.as_of))
            ids.append(ticket["id"])
    if not fired_all:
        return None
    lead = rules.lead(fired_all)
    return {"item": item["item"], "item_id": item["id"], "reported": item["reported_words"] or st.label(item["reported"]),
            "reported_category": item["reported"], "evidence": "; ".join(lines), "risk": rules.RULES[lead][1],
            "rule": lead, "rule_name": rules.RULES[lead][0], "rules": sorted(fired_all, key=lambda r: int(r[1:])),
            "tickets": ids, "quote": item["snippet"], "headline": bool(item.get("headline")),
            "order": int(item["id"][1:])}


def _unreported(tickets: list[dict], covered: set[str], ctx: rules.Ctx) -> list[dict]:
    out = []
    for t in tickets:
        if t["id"] in covered:
            continue
        f = rules.facts(t, [], ctx)
        overdue = f.due is not None and f.due < ctx.as_of and f.category not in (st.DONE, st.CANCELLED)
        if f.category == st.BLOCKED or overdue:
            out.append({"ticket": t["id"], "evidence": evidence.ticket(t, f, ctx.as_of),
                        "_key": (f.category != st.BLOCKED, f.due or ctx.as_of)})
    out.sort(key=lambda u: u["_key"])
    return [{k: v for k, v in u.items() if k != "_key"} for u in out[:MAX_UNREPORTED]]
