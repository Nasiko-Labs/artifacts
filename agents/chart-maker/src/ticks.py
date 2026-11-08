"""Pure: "nice number" axis ticks (1, 2 or 5 x 10^k), with zero always on the axis."""

from __future__ import annotations

import math

TARGET_TICKS = 5


def nice_step(span: float, target: int = TARGET_TICKS) -> float:
    raw = span / target
    magnitude = 10 ** math.floor(math.log10(raw))
    norm = raw / magnitude
    factor = 1 if norm <= 1 else 2 if norm <= 2 else 5 if norm <= 5 else 10
    return factor * magnitude


def ticks(low: float, high: float, target: int = TARGET_TICKS) -> tuple[float, ...]:
    """Ticks covering [min(low, 0), max(high, 0)]."""
    low, high = min(low, 0.0), max(high, 0.0)
    if high == low:
        high = low + 1
    step = nice_step(high - low, target)
    places = max(0, -math.floor(math.log10(step)))
    first, last = math.floor(low / step + 1e-9), math.ceil(high / step - 1e-9)
    return tuple(round(k * step, places) + 0.0 for k in range(first, last + 1))
