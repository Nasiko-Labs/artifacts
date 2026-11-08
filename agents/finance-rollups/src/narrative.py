"""Pure: the code check on the month-end narrative the model wrote, and the code-assembled template used when that
check fails. Every number in the narrative must be one the renderer prints for this report; causes and forecasts are
refused (a ledger variance may only be linked to its listed candidate explanations, worded as possibilities)."""

from __future__ import annotations

import json
import re

import months as m
import render
from render import Report

MAX_CHARS = 900
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_CAUSAL = re.compile(r"\b(?:because|due to|driven by|thanks to|as a result of|caused by|led by|owing to|resulted? "
                     r"from|will|expect\w*|forecast\w*|project(?:ed|ion)s?|likely)\b", re.IGNORECASE)


def facts_json(report: Report) -> str:
    return json.dumps(render.facts(report), ensure_ascii=False)


def allowed_numbers(report: Report) -> frozenset[str]:
    """Every number token the rendered reply and the facts contain, with and without sign and separators."""
    texts = (render.render_report(report, with_hint=False), facts_json(report))
    tokens = {t for text in texts for t in _NUMBER.findall(text)}
    return frozenset({v for t in tokens for v in _variants(t)})


def _variants(token: str) -> set[str]:
    bare = token.lstrip("+-")
    return {token, bare, bare.replace(",", ""), bare.rstrip("0").rstrip(".") if "." in bare else bare}


def problem(text: str, report: Report) -> str | None:
    """Why the narrative cannot be used, or None."""
    body = (text or "").strip()
    if not body or len(body) > MAX_CHARS:
        return "empty or too long"
    allowed = allowed_numbers(report)
    stray = [t for t in _NUMBER.findall(body) if t.lstrip("+-").rstrip(".,") not in allowed
             and t.lstrip("+-").rstrip(".,").replace(",", "") not in allowed]
    if stray:
        return f"numbers not in the rollup: {', '.join(stray[:3])}"
    if _CAUSAL.search(body):
        return "claims a cause or a forecast"
    return None


def template(report: Report) -> str:
    """The code-assembled narrative: always true, never clever."""
    sentences: list[str] = []
    for r in render.latest_per_currency(report):
        sentences.append(f"In {m.label(r.month)}, {r.currency} gross volume was {render.money(r.gross)} "
                         f"({render.signed_pct(r.mom_gross_pct)} month over month) and net was {render.money(r.net)} "
                         f"({render.signed_pct(r.mom_net_pct)}).")
        sentences.append(f"The refund rate was {render.pct(r.refund_rate)}, the fee rate {render.pct(r.fee_rate)}, "
                         f"with {r.dispute_count} dispute(s).")
    ledger = render.facts(report)["ledger"]
    if ledger:
        sentences.append(f"{ledger['items_tied_out']} of {ledger['items_compared']} ledger item(s) tie out.")
        worst = ledger["largest_unexplained"]
        if worst:
            sentences.append(f"The largest unexplained variance is {worst['variance']} on {worst['item']} in "
                             f"{worst['month']}.")
    caveats = [c for c, on in (("these are test-mode figures", report.livemode is False and report.source == "account"),
                               ("some months are partial", bool(report.partial_months))) if on]
    if caveats:
        sentences.append("Note: " + "; ".join(caveats) + ".")
    return " ".join(sentences)
