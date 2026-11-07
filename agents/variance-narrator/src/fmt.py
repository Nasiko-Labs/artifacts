"""Pure: every figure the reply or the model ever sees, formatted once here (`$124.3K`, `$1.20M`, `+12.4%`).
The model copies these strings; it never formats a number itself."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_BILLION = Decimal(999995000)   # rounds to 1000.00M, so it is shown in B
_MILLION = Decimal(999950)      # rounds to 1000.0K, so it is shown in M
_THOUSAND = Decimal(1_000)


def _q(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _trim(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def money(value: Decimal, symbol: str = "", signed: bool = False) -> str:
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    v = abs(value)
    if v >= _BILLION:
        body = f"{_q(v / Decimal(10**9), '0.01')}B"
    elif v >= _MILLION:
        body = f"{_q(v / Decimal(10**6), '0.01')}M"
    elif v >= _THOUSAND:
        body = f"{_trim(_q(v / _THOUSAND, '0.1'))}K"
    else:
        rounded = _q(v, "0.01")
        body = f"{int(rounded):,}" if rounded == rounded.to_integral_value() else f"{rounded:,.2f}"
    return f"{sign}{symbol}{body}"


def pct(value: Decimal | None, signed: bool = False) -> str:
    if value is None:
        return "n/a, no budget"
    rounded = _q(value, "0.1")
    sign = "+" if signed and rounded > 0 else ""
    if rounded == 0:
        rounded = abs(rounded)
    return f"{sign}{rounded}%"


def plain_number(value: Decimal) -> str:
    """A threshold or similar small figure: `10`, `5.5`."""
    return _trim(_q(value, "0.01"))
