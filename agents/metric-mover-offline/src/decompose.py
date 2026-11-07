"""Pure: decompose the change by one grouping of segments.

Additive: `c_i = a_i − b_i`, `share_i = c_i / Δ` (n/a when |Δ| < 1% of B: gross movement instead).
Rate: `r_i = num_i / den_i`, weights `w0_i, w1_i` (share of the denominator), overall `R0, R1`, and the exact
split `ΔR = Σ mix_i + Σ rate_i` with `mix_i = (w1_i − w0_i)(r0_i − R0)` and `rate_i = w1_i (r1_i − r0_i)`.
A segment with no denominator on one side takes that side's rate from the other side, so it contributes only
mix and the split stays exact. Per-segment significance: two-proportion z-test.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import stats
from frame import Row

NEAR_ZERO = 0.01
DRIVER_SHARE = 0.7


@dataclass(frozen=True)
class Seg:
    name: str
    b: float                  # additive value, or the rate (fraction) before
    a: float
    effect: float             # additive c_i, or mix_i + rate_i
    share: float | None       # effect / net change, in percent; None when the net change is near zero
    new: bool = False
    vanished: bool = False
    n0: float = 0.0           # rate only: denominators, weights, mix/rate effects and the z-test
    n1: float = 0.0
    w0: float = 0.0
    w1: float = 0.0
    mix: float = 0.0
    rate: float = 0.0
    test: stats.ZTest | None = None


@dataclass(frozen=True)
class Breakdown:
    dim: str
    kind: str
    segs: tuple[Seg, ...]             # sorted by |effect|, largest first
    before: float                     # B, or R0
    after: float
    delta: float
    near_zero: bool
    conc: stats.Concentration
    h: float
    explained: float
    total_mix: float = 0.0
    total_rate: float = 0.0
    driver: str = ""                  # rate-driven | mix-driven | both
    simpson: bool = False
    simpson_share: float = 0.0

    @property
    def mix_share(self) -> float:
        whole = abs(self.total_mix) + abs(self.total_rate)
        return abs(self.total_mix) / whole if whole else 0.0

    @property
    def rate_share(self) -> float:
        whole = abs(self.total_mix) + abs(self.total_rate)
        return abs(self.total_rate) / whole if whole else 0.0


def group(rows: Sequence[Row], key: Callable[[Row], str]) -> dict[str, list[float]]:
    sums: dict[str, list[float]] = {}
    for row in rows:
        current = sums.setdefault(key(row), [0.0] * len(row.vals))
        sums[key(row)] = [a + b for a, b in zip(current, row.vals, strict=True)]
    return sums


def breakdown(dim: str, kind: str, groups: dict[str, list[float]], min_n: int) -> Breakdown:
    return _additive(dim, groups) if kind == "additive" else _rate(dim, groups, min_n)


def _finish(dim: str, kind: str, segs: list[Seg], before: float, after: float, delta: float, near_zero: bool,
            **extra) -> Breakdown:
    ordered = tuple(sorted(segs, key=lambda s: (-abs(s.effect), s.name)))
    effects = [s.effect for s in ordered]
    return Breakdown(dim, kind, ordered, before, after, delta, near_zero, stats.concentration(effects, delta),
                     stats.herfindahl(effects), stats.explained_top3(effects), **extra)


def _additive(dim: str, groups: dict[str, list[float]]) -> Breakdown:
    total_b = sum(v[0] for v in groups.values())
    total_a = sum(v[1] for v in groups.values())
    delta = total_a - total_b
    near_zero = abs(delta) < NEAR_ZERO * abs(total_b) if total_b else delta == 0
    segs = [Seg(name, b, a, a - b, None if near_zero else (a - b) / delta * 100, new=b == 0 and a > 0,
                vanished=a == 0 and b > 0) for name, (b, a) in groups.items()]
    return _finish(dim, "additive", segs, total_b, total_a, delta, near_zero)


def _rate(dim: str, groups: dict[str, list[float]], min_n: int) -> Breakdown:
    live = {k: v for k, v in groups.items() if v[1] > 0 or v[3] > 0}
    den0 = sum(v[1] for v in live.values())
    den1 = sum(v[3] for v in live.values())
    r_0 = sum(v[0] for v in live.values()) / den0 if den0 else 0.0
    r_1 = sum(v[2] for v in live.values()) / den1 if den1 else 0.0
    delta = r_1 - r_0
    near_zero = abs(delta) < NEAR_ZERO * abs(r_0) if r_0 else delta == 0
    segs = []
    for name, (x0, n0, x1, n1) in live.items():
        w0, w1 = (n0 / den0 if den0 else 0.0), (n1 / den1 if den1 else 0.0)
        r0 = x0 / n0 if n0 else x1 / n1
        r1 = x1 / n1 if n1 else r0
        mix, rate = (w1 - w0) * (r0 - r_0), w1 * (r1 - r0)
        segs.append(Seg(name, r0, r1, mix + rate, None if near_zero else (mix + rate) / delta * 100,
                        new=n0 == 0, vanished=n1 == 0, n0=n0, n1=n1, w0=w0, w1=w1, mix=mix, rate=rate,
                        test=stats.ztest(x0, n0, x1, n1, min_n)))
    total_mix, total_rate = sum(s.mix for s in segs), sum(s.rate for s in segs)
    whole = abs(total_mix) + abs(total_rate)
    driver = ("rate-driven" if whole and abs(total_rate) >= DRIVER_SHARE * whole else
              "mix-driven" if whole and abs(total_mix) >= DRIVER_SHARE * whole else "both")
    against = sum(s.w1 for s in segs if delta and (s.a - s.b) * delta < 0)
    simpson = bool(delta) and against >= 0.6
    return _finish(dim, "rate", segs, r_0, r_1, delta, near_zero, total_mix=total_mix, total_rate=total_rate,
                   driver=driver, simpson=simpson, simpson_share=against)
