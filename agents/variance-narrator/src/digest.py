"""Pure: the digest. Every figure the narrative may use, pre-formatted once, with ids `v1..` for the lines the
model may cite. The prompt shows `as_text()`; the checks accept only number tokens found in it."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fmt
from variance import FLAT, Analysis, Item

LENGTHS = {"short": (0, 60), "default": (100, 160), "board": (80, 120)}
NUMBER = re.compile(r"[$€£¥₹]?[+-]?\d[\d,]*(?:\.\d+)?[KMB]?%?")
VERDICT = {"F": "favourable", "U": "unfavourable", FLAT: "on budget"}


def norm_number(token: str) -> str:
    return re.sub(r"[$€£¥₹+,-]", "", token).rstrip(".")


def number_tokens(text: str) -> set[str]:
    return {norm_number(t) for t in NUMBER.findall(text or "")}


@dataclass(frozen=True)
class Line:
    id: str
    item: Item
    amount: str          # |variance|, e.g. $190K
    signed: str          # +$190K
    pct: str             # 38.0% (absolute) or "n/a, no budget"
    signed_pct: str      # +38.0%
    budget: str
    actual: str

    @property
    def word(self) -> str:
        return "over" if self.item.variance_abs > 0 else "under"

    @property
    def verdict(self) -> str:
        return VERDICT[self.item.direction]

    @property
    def has_pct(self) -> bool:
        return self.item.variance_pct is not None


def line(id_: str, item: Item, symbol: str) -> Line:
    p = item.variance_pct
    return Line(id_, item, fmt.money(abs(item.variance_abs), symbol), fmt.money(item.variance_abs, symbol, True),
                fmt.pct(abs(p) if p is not None else None), fmt.pct(p, True), fmt.money(item.budget, symbol),
                fmt.money(item.actual, symbol))


@dataclass(frozen=True)
class Digest:
    analysis: Analysis
    symbol: str
    period: str
    length: str
    lines: tuple[Line, ...]       # the biggest variances, largest first
    offsets: tuple[Line, ...]

    @property
    def total_actual(self) -> str:
        return fmt.money(self.analysis.totals.actual, self.symbol)

    @property
    def total_budget(self) -> str:
        return fmt.money(self.analysis.totals.budget, self.symbol)

    @property
    def total_amount(self) -> str:
        return fmt.money(abs(self.analysis.totals.variance_abs), self.symbol)

    @property
    def total_pct(self) -> str:
        p = self.analysis.totals.variance_pct
        return fmt.pct(abs(p) if p is not None else None)

    @property
    def total_word(self) -> str:
        return "over" if self.analysis.totals.variance_abs > 0 else "under"

    @property
    def total_verdict(self) -> str:
        return VERDICT[self.analysis.totals.direction]

    @property
    def unexplained(self) -> tuple[str, ...]:
        return tuple(i.name for i in self.analysis.unexplained)

    @property
    def ids(self) -> set[str]:
        return {ln.id for ln in (*self.lines, *self.offsets)}

    def as_text(self) -> str:
        t = self.analysis.totals
        rows = [f"PERIOD: {self.period or 'not stated (do not name one)'}",
                f"TOTALS: actual {self.total_actual}; budget {self.total_budget}; variance {self.total_amount} "
                f"({self.total_pct}) {self.total_word} budget; {self.total_verdict}"
                if t.direction != FLAT else
                f"TOTALS: actual {self.total_actual}; budget {self.total_budget}; on budget",
                "BIGGEST VARIANCES (largest first):"]
        rows += [_describe(ln) for ln in self.lines] or ["none: no line crossed the materiality threshold"]
        rows.append("OFFSETS (opposite direction to the total): "
                    + ("; ".join(_describe(ln) for ln in self.offsets) or "none"))
        rows.append("STILL TO EXPLAIN: " + (", ".join(self.unexplained) or "none"))
        low, high = LENGTHS[self.length]
        rows.append(f"LENGTH: {self.length}, {low}-{high} words" if low else f"LENGTH: {self.length}, at most {high} words")
        return "\n".join(rows)

    def allowed_numbers(self) -> set[str]:
        return number_tokens(self.as_text())


def _describe(ln: Line) -> str:
    pct = f" ({ln.pct})" if ln.has_pct else " (no budget set)"
    driver = f'driver: "{ln.item.driver}"' if ln.item.driver else "driver: not provided"
    return (f"{ln.id} | {ln.item.name} | budget {ln.budget} | actual {ln.actual} | {ln.amount}{pct} {ln.word} budget"
            f" | {ln.verdict} | {driver}")


def build(analysis: Analysis, symbol: str, period: str, length: str) -> Digest:
    lines = tuple(line(f"v{n}", item, symbol) for n, item in enumerate(analysis.biggest, start=1))
    by_name = {ln.item.name: ln for ln in lines}
    offsets, extra = [], 0
    for item in analysis.offsets:
        if item.name in by_name:
            offsets.append(by_name[item.name])
        else:
            extra += 1
            offsets.append(line(f"o{extra}", item, symbol))
    return Digest(analysis, symbol, period, length if length in LENGTHS else "default", lines, tuple(offsets))
