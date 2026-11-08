"""Pure: ranking across services and each service's share of the period's errors.

Order: status severity (exhausted, fast burn, slow burn, elevated, healthy, insufficient data), then hours to
exhaust ascending (unknown last), then current burn descending, then name. Shares are floored percents, so they
never sum above 100."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal

from burn import NON_HEALTHY, SEVERITY, Forecast

_NO_HOURS = Decimal("Infinity")


def _key(f: Forecast) -> tuple:
    hours = f.hours_to_exhaust if f.hours_to_exhaust is not None else _NO_HOURS
    burn = -(f.burn_current if f.burn_current is not None else Decimal(-1))
    return SEVERITY[f.status], hours, burn, f.name.casefold()


def ranked(forecasts: Sequence[Forecast]) -> tuple[Forecast, ...]:
    return tuple(sorted(forecasts, key=_key))


def shares(forecasts: Sequence[Forecast]) -> Mapping[str, int]:
    """Service -> floor(percent of all services' period errors); 0 for every service when there are none."""
    total = sum((f.errors_period for f in forecasts), Decimal(0))
    if total <= 0:
        return {f.name: 0 for f in forecasts}
    return {f.name: int((f.errors_period * 100 / total).to_integral_value(ROUND_FLOOR)) for f in forecasts}


def top_burners(forecasts: Sequence[Forecast], limit: int = 2) -> tuple[Forecast, ...]:
    """The first `limit` services, in rank order, that are not healthy."""
    return tuple(f for f in ranked(forecasts) if f.status in NON_HEALTHY)[:limit]
