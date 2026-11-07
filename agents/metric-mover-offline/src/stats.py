"""Pure statistics: the two-proportion z-test, concentration of a change across segments, the
Herfindahl index and the share explained by the top three segments. Standard library only."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_MIN_N = 100
Z_CRITICAL = 1.96
CONCENTRATED_TOP1 = 0.5
BROAD_TOP3 = 0.5
OFFSET_SHARE = 0.25
SIGNIFICANT, NOISE, INSUFFICIENT, NOT_TESTABLE = "significant", "within noise", "insufficient data", "not testable"


@dataclass(frozen=True)
class ZTest:
    z: float
    p: float
    label: str


def ztest(x0: float, n0: float, x1: float, n1: float, min_n: int = DEFAULT_MIN_N) -> ZTest:
    """Two-sided two-proportion z-test of x1/n1 against x0/n0, pooled variance."""
    if n0 <= 0 or n1 <= 0:
        return ZTest(0.0, 1.0, NOT_TESTABLE)
    p0, p1 = x0 / n0, x1 / n1
    pooled = (x0 + x1) / (n0 + n1)
    variance = pooled * (1 - pooled) * (1 / n0 + 1 / n1)
    if variance <= 0:
        z = 0.0
    else:
        z = (p1 - p0) / math.sqrt(variance)
    p = math.erfc(abs(z) / math.sqrt(2))
    if n0 < min_n or n1 < min_n:
        return ZTest(z, p, INSUFFICIENT)
    return ZTest(z, p, SIGNIFICANT if abs(z) >= Z_CRITICAL else NOISE)


@dataclass(frozen=True)
class Concentration:
    label: str                 # concentrated | mixed | broad
    top1: float                # share of gross movement (0..1)
    top2: float
    top3: float
    gross: float
    offsets: tuple[int, ...]   # indexes of segments moving against the net change, when they offset ≥ 25%
    offset_share: float        # their combined |effect| as a share of |net|


def concentration(effects: Sequence[float], net: float) -> Concentration:
    """`effects` per segment; `net` the overall change they sum to."""
    gross = sum(abs(e) for e in effects)
    ranked = sorted((abs(e) for e in effects), reverse=True)
    top = [sum(ranked[:k]) / gross if gross else 0.0 for k in (1, 2, 3)]
    against = [i for i, e in enumerate(effects) if net and e * net < 0]
    against_total = sum(abs(effects[i]) for i in against)
    offset_share = against_total / abs(net) if net else 0.0
    biggest_against = max((abs(effects[i]) for i in against), default=0.0)
    if top[0] >= CONCENTRATED_TOP1:
        label = "concentrated"
    elif top[2] < BROAD_TOP3 and (not net or biggest_against <= OFFSET_SHARE * abs(net)):
        label = "broad"
    else:
        label = "mixed"
    offsets = tuple(against) if offset_share >= OFFSET_SHARE else ()
    return Concentration(label, top[0], top[1], top[2], gross, offsets, offset_share)


def herfindahl(effects: Sequence[float]) -> float:
    gross = sum(abs(e) for e in effects)
    return sum((abs(e) / gross) ** 2 for e in effects) if gross else 0.0


def explained_top3(effects: Sequence[float]) -> float:
    gross = sum(abs(e) for e in effects)
    return sum(sorted((abs(e) for e in effects), reverse=True)[:3]) / gross if gross else 0.0
