"""Pure: the board paragraph's inputs (exact formatted values), the code check on what the model wrote,
and the code-assembled template used when that check fails. Every number in the paragraph must be one
the renderer prints for this report; causes and forecasts are refused."""

from __future__ import annotations

import json
import re

import mrr
import render
from mrr import Block
from render import Report

MAX_CHARS = 900
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_CAUSAL = re.compile(r"\b(?:because|due to|driven by|thanks to|as a result of|caused by|led by|owing to|"
                     r"will|expect\w*|forecast\w*|project(?:ed|ion)s?)\b", re.IGNORECASE)
_PRAISE = re.compile(r"\b(?:strong|excellent|great|healthy|impressive|robust|solid)\b", re.IGNORECASE)


def facts(block: Block, report: Report) -> dict:
    """The JSON the model words the paragraph from: values exactly as the reply shows them."""
    return {"period": report.label, "currency": block.currency, "mode": render.mode_label(report),
            "mrr": render.money(block.mrr1), "start_mrr": render.money(block.mrr0),
            "net_new_mrr": render.signed_money(block.net_new), "mom_growth": render.signed_pct(block.mom_pct),
            "arr": render.money(block.arr), "new_mrr": render.money(block.new_mrr),
            "expansion_mrr": render.maybe(block, block.expansion_mrr),
            "contraction_mrr": render.maybe(block, block.contraction_mrr),
            "churned_mrr": render.money(block.churned_mrr), "nrr": render.pct(block.nrr_pct),
            "grr": render.pct(block.grr_pct), "logo_churn": render.pct(block.logo_churn_pct),
            "revenue_churn": render.pct(block.revenue_churn_pct), "customers": block.customers1,
            "churned_customers": block.churned_customers, "approximate": report.approximate,
            "unsupported_items": block.unsupported_items, "tone": mrr.tone(block)}


def facts_json(block: Block, report: Report) -> str:
    return json.dumps(facts(block, report), ensure_ascii=False)


def allowed_numbers(report: Report) -> frozenset[str]:
    """Every number token the rendered reply and the facts contain, with and without sign and separators."""
    texts = [render.render_report(report, has_sheet_hint=False)]
    texts.extend(json.dumps(facts(b, report)) for b in report.blocks)
    tokens = {t for text in texts for t in _NUMBER.findall(text)}
    return frozenset({v for t in tokens for v in _variants(t)})


def _variants(token: str) -> set[str]:
    bare = token.lstrip("+-")
    return {token, bare, bare.replace(",", ""), bare.rstrip("0").rstrip(".") if "." in bare else bare}


def problem(paragraph: str, report: Report) -> str | None:
    """Why the paragraph cannot be used, or None."""
    text = (paragraph or "").strip()
    if not text or len(text) > MAX_CHARS:
        return "empty or too long"
    allowed = allowed_numbers(report)
    stray = [t for t in _NUMBER.findall(text) if t.lstrip("+-").rstrip(".,") not in allowed
             and t.lstrip("+-").replace(",", "") not in allowed]
    if stray:
        return f"numbers not in the metrics: {', '.join(stray[:3])}"
    if _CAUSAL.search(text):
        return "claims a cause or a forecast"
    if _PRAISE.search(text) and not _may_praise(report):
        return "praise the numbers do not support"
    return None


def _may_praise(report: Report) -> bool:
    return all((b.nrr_pct is not None and b.nrr_pct >= 100) or b.net_new > 0 for b in report.blocks)


def template(report: Report) -> str:
    """The code-assembled paragraph: always true, never clever."""
    sentences = []
    for b in report.blocks:
        change = {"up": "up", "down": "down", "flat": "roughly flat"}[mrr.tone(b)]
        sentences.append(f"In {report.label}, {b.currency} MRR was {render.money(b.mrr1)} ({change}, "
                         f"{render.signed_money(b.net_new)} or {render.signed_pct(b.mom_pct)} from "
                         f"{render.money(b.mrr0)}), an ARR of {render.money(b.arr)}.")
        if b.changes_known:
            sentences.append(f"New MRR was {render.money(b.new_mrr)}, expansion {render.money(b.expansion_mrr)}, "
                             f"contraction {render.money(b.contraction_mrr)} and churned MRR "
                             f"{render.money(b.churned_mrr)}.")
        else:
            sentences.append(f"New MRR was {render.money(b.new_mrr)} and churned MRR {render.money(b.churned_mrr)}.")
        sentences.append(f"Net revenue retention was {render.pct(b.nrr_pct)}, gross revenue retention "
                         f"{render.pct(b.grr_pct)} and logo churn {render.pct(b.logo_churn_pct)}.")
    caveat = _caveat(report)
    return " ".join([*sentences, caveat] if caveat else sentences)


def _caveat(report: Report) -> str:
    parts = []
    if report.livemode is False:
        parts.append("these are test-mode figures")
    if report.approximate:
        parts.append("plan changes older than 30 days are approximated")
    if any(b.unsupported_items for b in report.blocks):
        parts.append("usage-based items are excluded")
    return ("Note: " + "; ".join(parts) + ".") if parts else ""
