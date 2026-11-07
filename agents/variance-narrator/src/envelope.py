"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `narrative`,
`biggest_variances` and the other fields). `confidence` and `needs_human` are computed here from the run's
checks, never taken from the model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from digest import Digest, Line
from variance import Analysis

LOW_UNPARSED_SHARE = Decimal("0.2")
NEEDS_HUMAN_UNEXPLAINED_SHARE = Decimal("0.5")


@dataclass(frozen=True)
class Coverage:
    rows: int = 0
    unparseable: int = 0
    skipped: int = 0
    capped: int = 0
    blanks: int = 0


@dataclass(frozen=True)
class Narration:
    text: str
    source: str = "template"     # "model" | "template"
    fallback: bool = False       # the model was asked but its narrative failed the checks (or the call failed)
    replaced: int = 0            # unsupported-cause sentences replaced by "driver not provided"


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(round(value, 2))


def variance(ln: Line) -> dict:
    i = ln.item
    return {"name": i.name, "budget": _num(i.budget), "actual": _num(i.actual), "variance_abs": _num(i.variance_abs),
            "variance_pct": _num(i.variance_pct), "direction": i.direction, "kind": i.kind,
            "explanation": i.driver or None, "explanation_source": i.driver_source,
            "display": {"budget": ln.budget, "actual": ln.actual, "variance": ln.signed, "pct": ln.signed_pct}}


def confidence(a: Analysis, narration: Narration, coverage: Coverage) -> str:
    unread = coverage.unparseable + coverage.skipped
    if a.sign_guessed or a.reconciled is False or (coverage.rows and unread / coverage.rows > LOW_UNPARSED_SHARE):
        return "low"
    if narration.fallback or narration.replaced or a.unexplained or unread or coverage.capped:
        return "medium"
    return "high"


def unexplained_share(a: Analysis) -> Decimal:
    whole = sum((abs(i.variance_abs) for i in a.items), Decimal(0))
    if whole == 0:
        return Decimal(0)
    return sum((abs(i.variance_abs) for i in a.unexplained), Decimal(0)) / whole


def summary(d: Digest) -> str:
    a = d.analysis
    head = (f"Total actual {d.total_actual} vs budget {d.total_budget}: on budget" if a.totals.direction == "flat"
            else f"Total actual {d.total_actual} vs budget {d.total_budget}: {d.total_amount} ({d.total_pct}) "
                 f"{d.total_word}, {d.total_verdict}")
    return f"{head}; {len(d.lines)} material variance(s), {len(a.unexplained)} without a driver."


def build(d: Digest, narration: Narration, coverage: Coverage) -> dict:
    a, t = d.analysis, d.analysis.totals
    level = confidence(a, narration, coverage)
    biggest = [variance(ln) for ln in d.lines]
    return {
        "summary": summary(d), "items": biggest, "narrative": narration.text, "biggest_variances": biggest,
        "totals": {"budget": _num(t.budget), "actual": _num(t.actual), "variance_abs": _num(t.variance_abs),
                   "variance_pct": _num(t.variance_pct), "direction": t.direction,
                   "display": {"budget": d.total_budget, "actual": d.total_actual, "variance": d.total_amount,
                               "pct": d.total_pct}},
        "offsets": [variance(ln) for ln in d.offsets], "unexplained": list(d.unexplained), "caveats": list(a.caveats),
        "sign_convention": a.sign_convention, "period": d.period or None, "length": d.length,
        "threshold_pct": float(a.options.threshold), "only": a.options.only or None,
        "narrative_source": narration.source, "confidence": level,
        "needs_human": level == "low" or unexplained_share(a) > NEEDS_HUMAN_UNEXPLAINED_SHARE,
    }
