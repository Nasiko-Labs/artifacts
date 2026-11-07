"""Pure: normalised monthly recurring revenue, in the currency's minor units, computed in code.

    base = unit_amount_minor * quantity
    month: base / interval_count            year: base / (12 * interval_count)
    week:  base * 52 / (12 * interval_count) day:  base * 365 / (12 * interval_count)
    then percent_off applied, then rounded half up to an integer.

Tiered and metered items have no fixed monthly amount: they are excluded and counted. Amounts in
different currencies are never summed."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from records import Item, Sub

_PER_MONTH = {"month": Fraction(1), "year": Fraction(1, 12), "week": Fraction(52, 12), "day": Fraction(365, 12)}
ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND",
                          "VUV", "XAF", "XOF", "XPF"})


@dataclass(frozen=True)
class Monthly:
    minor: int
    excluded: int = 0   # tiered or metered items left out


def half_up(value: Fraction) -> int:
    return int((Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def item_monthly(item: Item, interval: str, interval_count: int, percent_off: float | None = None) -> int | None:
    """One item's monthly amount in minor units, or None when it has no fixed amount (excluded)."""
    if item.unit_amount_minor is None or item.billing_scheme == "tiered" or item.usage_type == "metered":
        return None
    factor = _PER_MONTH.get(interval)
    if factor is None:
        return None
    exact = Fraction(item.unit_amount_minor * item.quantity) * factor / max(interval_count, 1)
    if percent_off:
        exact = exact * (Fraction(100) - Fraction(str(percent_off))) / 100
    return half_up(exact)


def items_monthly(items: Iterable[Item], interval: str, interval_count: int,
                  percent_off: float | None = None) -> Monthly:
    amounts = [item_monthly(i, interval, interval_count, percent_off) for i in items]
    return Monthly(sum(a for a in amounts if a is not None), sum(1 for a in amounts if a is None))


def sub_monthly(sub: Sub) -> Monthly:
    return items_monthly(sub.items, sub.interval, sub.interval_count, sub.percent_off)


def exponent(currency: str) -> int:
    return 0 if currency.upper() in ZERO_DECIMAL else 2


def to_major(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-exponent(currency))


def to_minor(major: Decimal, currency: str) -> int:
    return int((major.scaleb(exponent(currency))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def money(minor: int, currency: str) -> str:
    """`300000, USD` → "3,000.00 USD"; the code is left off when the currency is unknown."""
    places = exponent(currency)
    text = f"{to_major(minor, currency):,.{places}f}"
    return f"{text} {currency}" if currency else text
