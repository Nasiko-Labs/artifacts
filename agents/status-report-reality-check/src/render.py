"""Pure: the markdown reply (or the fenced JSON envelope), rendered in code from the envelope."""

from __future__ import annotations

import json

import evidence
import rules
from statuses import label

MAX_ROWS = 40
MAX_CHILDREN = 30


def render(env: dict, view: list[dict] | None = None, heading: str = "", notes: tuple[str, ...] = ()) -> str:
    shown = env["disagreements"] if view is None else view
    high = sum(1 for d in env["disagreements"] if d["risk"] == rules.HIGH)
    count = len(env["disagreements"])
    title = (f"**Reality check (as of {env['as_of']}):** {env['report_items']} report items vs {env['tickets']} "
             f"tickets: {count} disagreement{'s' if count != 1 else ''}, {high} high")
    lines = [title]
    if heading:
        lines.append(f"_{heading}_")
    lines.append(_table(shown) if shown else _none_line(env, view))
    if view is None and env["unmatched"]:
        lines.append("**Not matched to any ticket:** " + ", ".join(evidence.clean(u["item"]) for u in env["unmatched"]))
    if view is None and "unreported_risks" in env:
        lines.append(_unreported(env["unreported_risks"]))
    if env["caveats"]:
        lines.append("**Caveats:** " + " ".join(env["caveats"]))
    lines.append(f"_Confidence: {env['confidence']}. Stale after {env['stale_days']} days, due soon within "
                 f"{env['soon_days']} days (change with \"stale after 7 days\"). I show both sides; the call is "
                 "yours._")
    return "\n\n".join([*lines, *notes])


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"


def render_tickets(item: dict, tickets: list[dict]) -> str:
    by_id = {t["id"]: t for t in tickets}
    matched = [by_id[i] for i in item["match"]["ids"] if i in by_id]
    if not matched:
        return f"No ticket is matched to '{evidence.clean(item['item'])}'."
    lines = [f"**Tickets matched to '{evidence.clean(item['item'])}'** ({item['match']['kind']} match):"]
    for ticket in matched:
        lines.append(f"- {evidence.child_line(ticket)}")
        children = [t for t in tickets if t.get("parent_id") == ticket["id"] and t["id"] != ticket["id"]]
        lines += [f"  - {evidence.child_line(c)}" for c in children[:MAX_CHILDREN]]
        if len(children) > MAX_CHILDREN:
            lines.append(f"  - … and {len(children) - MAX_CHILDREN} more")
    return "\n".join(lines)


def render_why(item: dict, disagreement: dict | None) -> str:
    name = evidence.clean(item["item"])
    if disagreement is None:
        reason = ("it has no ticket match" if item["match"]["kind"] == "none" else
                  "no status words I could map" if item["reported"] == "unknown" else
                  "the tickets fit what the report says")
        return f"'{name}' isn't flagged: {reason}."
    fired = "; ".join(f"{r} {rules.RULES[r][0].replace('_', ' ')} ({rules.RULES[r][1]})" for r in disagreement["rules"])
    return (f"**{name}** is flagged **{disagreement['risk']}**.\n\n"
            f"- The report says: \"{evidence.clean(disagreement['quote'], 200)}\" (read as {label(item['reported'])})\n"
            f"- The tickets say: {disagreement['evidence']}\n- Rules that fired: {fired}")


def render_unmatched(env: dict) -> str:
    if not env["unmatched"]:
        return "Every report item was matched to a ticket."
    return "**Not matched to any ticket:**\n" + "\n".join(
        f"- {evidence.clean(u['item'])} (reported: {evidence.clean(u['reported'], 60)})" for u in env["unmatched"])


def _table(rows: list[dict]) -> str:
    out = ["| Item | Reported | What the tickets say | Risk |", "|---|---|---|---|"]
    for d in rows[:MAX_ROWS]:
        out.append(f"| {evidence.clean(d['item'])} | {evidence.clean(d['reported'], 60)} | "
                   f"{d['evidence'].replace('|', '/')} | {d['risk']} |")
    if len(rows) > MAX_ROWS:
        out.append(f"| … {len(rows) - MAX_ROWS} more (ask \"as json\" for all) | | | |")
    return "\n".join(out)


def _none_line(env: dict, view: list[dict] | None) -> str:
    if view is not None and env["disagreements"]:
        return "None in this view."
    return "No disagreements: every compared item's reported status fits its tickets."


def _unreported(risks: list[dict]) -> str:
    if not risks:
        return "**Unreported at-risk tickets:** none."
    return "**Unreported at-risk tickets:**\n" + "\n".join(f"- {r['evidence']}" for r in risks)
