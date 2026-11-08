"""Pure: the burn maths, exact. Decimal arithmetic only; the model never sees a row and never computes a value.

For a service with SLO `S` (fraction) over a rolling period of `P` days (`P_h = 24 P`), as of its latest timestamp:
- burn rate over a window = (errors / total in the window) / (1 - S); 1.0 burns the budget exactly in one period;
- budget consumed = errors in the period / ((1 - S) x period traffic), the traffic extrapolated from the data's
  average when the data spans under 95% of the period;
- hours to exhaust = remaining fraction x P_h / current burn, the current burn being the 6 h rate when the last
  6 h carry at least 50 requests, else the 24 h rate (steady traffic assumed);
- status from multi-window thresholds in the style of the SRE Workbook's burn-rate alerting (paraphrased):
  exhausted; fast burn (1 h >= 14.4 and 6 h >= 6); slow burn (6 h >= 6 or 24 h >= 3); elevated (24 h or period
  > 1); healthy otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal

import buckets
from buckets import HOUR, Series
from slo import Slo

SHORT_WINDOWS_H = (1, 6, 24)
MIN_DATA_HOURS = 12
MIN_BUCKETS = 3
MIN_RECENT_EVENTS = Decimal(50)
FULL_PERIOD_SHARE = Decimal("0.95")
STALE_AFTER_HOURS = 6
FAST_1H, FAST_6H = Decimal("14.4"), Decimal(6)
SLOW_6H, SLOW_24H = Decimal(6), Decimal(3)
SUSTAINABLE = Decimal(1)
WORST_WINDOW_H = 6

EXHAUSTED, FAST, SLOW, ELEVATED, HEALTHY, INSUFFICIENT = (
    "exhausted", "fast_burn", "slow_burn", "elevated", "healthy", "insufficient_data")
SEVERITY = {EXHAUSTED: 0, FAST: 1, SLOW: 2, ELEVATED: 3, HEALTHY: 4, INSUFFICIENT: 5}
NON_HEALTHY = frozenset({EXHAUSTED, FAST, SLOW, ELEVATED})
ALERTING = frozenset({EXHAUSTED, FAST, SLOW})


@dataclass(frozen=True)
class Forecast:
    name: str
    slo: Slo
    status: str
    as_of: int
    data_age_h: Decimal
    span_h: Decimal
    burn: dict                      # {"h1", "h6", "h24", "period"} -> Decimal | None (None = no traffic)
    current_window: str | None      # "6h" | "24h" | None
    burn_current: Decimal | None
    remaining_frac: Decimal | None
    hours_to_exhaust: Decimal | None
    errors_period: Decimal
    total_period: Decimal
    worst_window: tuple[int, int] | None
    flags: tuple[str, ...]
    counts: dict = field(default_factory=dict)  # window key -> (total, errors), for "show the numbers"
    factor: Decimal = Decimal(1)

    @property
    def period_h(self) -> int:
        return 24 * (self.slo.period_days or 0)

    @property
    def beyond_period(self) -> bool:
        return self.hours_to_exhaust is not None and self.hours_to_exhaust > self.period_h

    @property
    def exhaust_at(self) -> int | None:
        if self.hours_to_exhaust is None or self.beyond_period:
            return None
        return self.as_of + int((self.hours_to_exhaust * HOUR).to_integral_value(ROUND_HALF_UP))


def round_to(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _rate(errors: Decimal, total: Decimal) -> Decimal | None:
    return None if total <= 0 else errors / total


def forecast(series: Series, slo: Slo, now: int) -> Forecast:
    """The forecast for one service as of its latest timestamp; `now` is the turn's clock (Unix seconds)."""
    as_of = series.last
    budget = 1 - slo.target
    period_h = 24 * slo.period_days
    span_h = Decimal(series.span_seconds) / HOUR
    age_h = Decimal(max(now - as_of, 0)) / HOUR
    flags = _input_flags(series, age_h)
    windows = {f"h{w}": buckets.window(series, as_of - w * HOUR, as_of) for w in SHORT_WINDOWS_H}
    total_p, errors_p = buckets.window(series, as_of - period_h * HOUR, as_of)
    burn = {key: _burn(e, t, budget) for key, (t, e) in windows.items()}
    burn = {**burn, "period": _burn(errors_p, total_p, budget)}
    base = Forecast(series.service, slo, HEALTHY, as_of, age_h, span_h, burn, None, None, None, None, errors_p,
                    total_p, _worst_window(series, as_of), flags, {**windows, "period": (total_p, errors_p)})
    if span_h < MIN_DATA_HOURS or len(series.buckets) < MIN_BUCKETS:
        return replace(base, status=INSUFFICIENT, flags=(*flags, INSUFFICIENT), worst_window=None)
    remaining, extrapolated = _remaining(series, total_p, errors_p, budget, span_h, period_h)
    flags = (*flags, "extrapolated") if extrapolated else flags
    total_6 = windows["h6"][0]
    if total_6 <= 0:
        flags = (*flags, "no_recent_traffic")
    window_key = "h6" if total_6 >= MIN_RECENT_EVENTS else "h24"
    current = burn[window_key] if total_6 > 0 else None
    status = _status(burn, remaining)
    return replace(base, status=status, current_window=window_key[1:] + "h" if current is not None else None,
                   burn_current=current, remaining_frac=remaining,
                   hours_to_exhaust=hours_to_exhaust(remaining, period_h, current), flags=flags)


def _burn(errors: Decimal, total: Decimal, budget: Decimal) -> Decimal | None:
    rate = _rate(errors, total)
    return None if rate is None else rate / budget


def _input_flags(series: Series, age_h: Decimal) -> tuple[str, ...]:
    flags = ("assumed_uniform_traffic",) if series.assumed_uniform else ()
    flags = (*flags, "looks_cumulative") if series.looks_cumulative else flags
    return (*flags, "stale") if age_h > STALE_AFTER_HOURS else flags


def _remaining(series: Series, total_p: Decimal, errors_p: Decimal, budget: Decimal, span_h: Decimal,
               period_h: int) -> tuple[Decimal | None, bool]:
    extrapolated = span_h < FULL_PERIOD_SHARE * period_h
    if extrapolated:
        total_all, _ = buckets.totals(series)
        projected = total_all / span_h * period_h
    else:
        projected = total_p
    allowed = budget * projected
    if allowed <= 0:
        return None, extrapolated
    return 1 - errors_p / allowed, extrapolated


def _at_least(value: Decimal | None, bar: Decimal) -> bool:
    return value is not None and value >= bar


def _above(value: Decimal | None, bar: Decimal) -> bool:
    return value is not None and value > bar


def _status(burn: dict, remaining: Decimal | None) -> str:
    if remaining is not None and remaining <= 0:
        return EXHAUSTED
    if _at_least(burn["h1"], FAST_1H) and _at_least(burn["h6"], FAST_6H):
        return FAST
    if _at_least(burn["h6"], SLOW_6H) or _at_least(burn["h24"], SLOW_24H):
        return SLOW
    if _above(burn["h24"], SUSTAINABLE) or _above(burn["period"], SUSTAINABLE):
        return ELEVATED
    return HEALTHY


def hours_to_exhaust(remaining: Decimal | None, period_h: int, current: Decimal | None) -> Decimal | None:
    """0 when exhausted; None when the burn is zero or unknown ("not at this rate"); else one decimal."""
    if remaining is None or current is None:
        return None
    if remaining <= 0:
        return Decimal("0.0")
    if current <= 0:
        return None
    return round_to(remaining * period_h / current, 1)


def what_if(f: Forecast, factor: Decimal) -> Forecast:
    """The same forecast with the current burn multiplied by `factor`; status and windows unchanged."""
    if f.burn_current is None:
        return replace(f, factor=factor)
    current = f.burn_current * factor
    return replace(f, burn_current=current, factor=factor,
                   hours_to_exhaust=hours_to_exhaust(f.remaining_frac, f.period_h, current))


def _worst_window(series: Series, as_of: int) -> tuple[int, int] | None:
    """The 6-hour slice of the last 24 h with the highest error ratio (latest wins a tie); None without errors."""
    best: tuple[Decimal, int] | None = None
    for k in range(24 // WORST_WINDOW_H):
        end = as_of - k * WORST_WINDOW_H * HOUR
        total, errors = buckets.window(series, end - WORST_WINDOW_H * HOUR, end)
        ratio = _rate(errors, total)
        if ratio is not None and ratio > 0 and (best is None or ratio > best[0]):
            best = (ratio, end)
    return None if best is None else (best[1] - WORST_WINDOW_H * HOUR, best[1])
