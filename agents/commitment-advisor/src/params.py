"""Pure: the free-text options, parsed in code (never by the model): "discount 28%" (1-year), "3y discount
48%", "min utilization 90%", "1y" / "3 years" (term), "compute only". Values outside sane bounds are ignored."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from constants import (
    DEFAULT_DISCOUNTS,
    MAX_DISCOUNT_PCT,
    MIN_DISCOUNT_PCT,
    MIN_UTILIZATION,
    MIN_UTILIZATION_PCT,
)

_NUM = r"(\d{1,2}(?:\.\d+)?)\s*%"
_DISCOUNT_AFTER = re.compile(rf"discount\b[^\d%\n]{{0,20}}?{_NUM}", re.IGNORECASE)
_DISCOUNT_BEFORE = re.compile(rf"{_NUM}\s*(?:off|discount)", re.IGNORECASE)
_THREE = re.compile(r"\b(?:3|three)[\s-]*(?:y|yr|yrs|year|years)\b", re.IGNORECASE)
_TERM = re.compile(r"\b(1|3|one|three)[\s-]*(?:y|yr|yrs|year|years)\b", re.IGNORECASE)
_MIN_UTIL = re.compile(r"min(?:imum)?\.?\s+utili[sz]ation\D{0,12}?(\d{2,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_COMPUTE_ONLY = re.compile(r"\bcompute only\b|\bonly (?:the )?compute\b", re.IGNORECASE)
LOOKBACK_CHARS = 30


@dataclass(frozen=True)
class Params:
    discount_1y: float | None = None
    discount_3y: float | None = None
    min_utilization: float = MIN_UTILIZATION
    term: int | None = None
    compute_only: bool = False

    @property
    def quotes(self) -> bool:
        return self.discount_1y is not None or self.discount_3y is not None

    def discounts(self) -> dict[int, float]:
        return {1: self.discount_1y if self.discount_1y is not None else DEFAULT_DISCOUNTS[1],
                3: self.discount_3y if self.discount_3y is not None else DEFAULT_DISCOUNTS[3]}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Params:
        if not isinstance(data, dict):
            return cls()
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


def _discounts(text: str) -> tuple[dict[int, float], str]:
    found: dict[int, float] = {}
    spans = []
    for pattern in (_DISCOUNT_AFTER, _DISCOUNT_BEFORE):
        for m in pattern.finditer(text):
            pct = float(m.group(1))
            if not MIN_DISCOUNT_PCT <= pct <= MAX_DISCOUNT_PCT:
                continue
            before = text[max(0, m.start() - LOOKBACK_CHARS):m.start() + len("discount")]
            terms = list(_TERM.finditer(before))
            term = 3 if terms and _THREE.fullmatch(terms[-1].group(0)) else 1
            found.setdefault(term, round(pct / 100, 4))
            spans.append((max(0, m.start() - LOOKBACK_CHARS), m.end()))
    rest = text
    for lo, hi in spans:
        rest = rest[:lo] + " " * (hi - lo) + rest[hi:]
    return found, rest


def parse(text: str) -> Params:
    discounts, rest = _discounts(text or "")
    util = _MIN_UTIL.search(rest)
    rest = _MIN_UTIL.sub(" ", rest)
    term = _TERM.search(rest)
    min_util = MIN_UTILIZATION
    if util and MIN_UTILIZATION_PCT <= float(util.group(1)) <= 100:
        min_util = round(float(util.group(1)) / 100, 4)
    term_years = None if term is None else (3 if term.group(1).casefold() in ("3", "three") else 1)
    return Params(discounts.get(1), discounts.get(3), min_util, term_years, bool(_COMPUTE_ONLY.search(rest)))


def merge(old: Params, new: Params) -> Params:
    """Options given now win; the rest keep what was used before."""
    return replace(old,
                   discount_1y=new.discount_1y if new.discount_1y is not None else old.discount_1y,
                   discount_3y=new.discount_3y if new.discount_3y is not None else old.discount_3y,
                   min_utilization=new.min_utilization if new.min_utilization != MIN_UTILIZATION
                   else old.min_utilization,
                   term=new.term if new.term is not None else old.term,
                   compute_only=new.compute_only or old.compute_only)


def is_empty(p: Params) -> bool:
    return p == Params()
