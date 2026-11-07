"""Pure: follow-ups on a stored result, parsed in code.

    "only the violations", "only the high-risk ones", "cost table", "assumptions", "as json" -> re-render
    "set limit $300", "no limit", "assume 500 GB through NAT", "region eu-west-1", "environment dev"
                                                                                         -> recompute
    a message naming a stored resource address                                          -> that line
All of these cost zero model calls."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import envelope
import options
from verdict import money

COMMAND_MAX_WORDS = 14
_PLAIN = re.compile(r"[^a-z0-9$\-. ]+")
_VIEWS = (
    ("high", re.compile(r"\bhigh[- ]risk\b|\bmedium(?: ones| only| violations)?\b|\bserious\b|\bimportant ones\b")),
    ("violations", re.compile(r"\bviolations?\b|\bwaste\b|\brules?\b|\bsources?\b|\bevidence\b|\bfindings?\b")),
    ("lines", re.compile(r"\bcost table\b|\btable\b|\blines?\b|\bbreakdown\b|\bitemi[sz]ed\b|\bper resource\b")),
    ("assumptions", re.compile(r"\bassumptions?\b|\bunpriced\b")),
    ("default", re.compile(r"\b(?:as json|json only|in json|full report|show all|everything|summary|report)\b")),
)


@dataclass(frozen=True)
class Command:
    kind: str                                   # view | recompute
    view: str = "default"
    changes: dict = field(default_factory=dict)


def plain(text: str) -> str:
    return " ".join(_PLAIN.sub(" ", (text or "").casefold()).split())


def view_of(p: str) -> str | None:
    for view, pattern in _VIEWS:
        if pattern.search(p):
            return view
    return None


def parse(text: str) -> Command | None:
    p = plain(text)
    if not p or len(p.split()) > COMMAND_MAX_WORDS:
        return None
    changes = options.parse(text)
    if changes:
        return Command("recompute", view_of(p) or "default", changes)
    view = view_of(p)
    return Command("view", view) if view else None


def about_line(text: str, result: Mapping[str, Any]) -> str | None:
    """The stored line and violations of a resource the message names by address, else None."""
    low = (text or "").casefold()
    addresses = {ln["address"] for ln in result["lines"]} | {v["address"] for v in result["violations"]}
    named = sorted((a for a in addresses if a.casefold() in low), key=len, reverse=True)
    if not named:
        return None
    address = named[0]
    line = next((ln for ln in result["lines"] if ln["address"] == address), None)
    out = [f"**{address}**"]
    if line is not None:
        cost = money(line["monthly"]) + "/month" if line["basis"] == "priced" else line["basis"].replace("_", "-")
        detail = f" ({line['detail']})" if line["detail"] else ""
        out.append(f"- Estimate: {cost}{detail}, quantity {line['qty']}.")
        out += [f"- Assumption: {a}." for a in line["assumptions"]]
    for v in envelope.violations(result):
        if v["address"] == address:
            saving = f", ~ {money(v['est_monthly_saving'])}/month potential saving" if v["est_monthly_saving"] else ""
            out.append(f"- `{v['rule_id']}` {v['title']} (`{v['attribute']}` = `{v['evidence']}`){saving}. "
                       f"Fix: {v['fix']}")
    if len(out) == 1:
        out.append("- No cost line or violation for it.")
    return "\n".join(out)
