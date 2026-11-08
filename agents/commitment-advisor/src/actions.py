"""Pure: what to do with the commitments already held, per scope.

Expiring ones (ending within 90 days of the data end, or ended within the last 30): the renewal level X_renew
is the optimum on R'_t = max(0, H_t - E_nonexpiring - X_buy), compared with their cover L: >= 1.15 L renew_more,
0.85-1.15 L renew_same, 0.25-0.85 L renew_less, below 0.25 L let_lapse. The others: portfolio utilization
u = sum(min(H_t, E)) / (E * T); under 80% they are flagged underused with the wasted spend per month."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import optimise
from commitments import Held, Portfolio
from constants import (
    HOURS_PER_MONTH,
    RENEW_LESS_RATIO,
    RENEW_MORE_RATIO,
    RENEW_SAME_RATIO,
    UNDERUSED_UTILIZATION,
)
from usage import Usage


def classify_renewal(x_renew: float, cover: float) -> str:
    ratio = x_renew / cover if cover > 0 else 0.0
    if ratio >= RENEW_MORE_RATIO:
        return "renew_more"
    if ratio >= RENEW_SAME_RATIO:
        return "renew_same"
    if ratio >= RENEW_LESS_RATIO:
        return "renew_less"
    return "let_lapse"


def _entry(h: Held, action: str, **extra) -> dict:
    c = h.commitment
    return {"id": c.id, "type": c.kind, "scope": h.scope, "term_years": c.term_years, "action": action,
            "hourly_commit": round(h.od_hourly * (1 - h.discount), 2), "od_hourly": round(h.od_hourly, 4),
            "ends_in_days": h.days_left, "end_date": c.end.isoformat(), **extra}


def portfolio_utilization(series: Sequence[float], cover: float) -> float:
    return sum(min(v, cover) for v in series) / (cover * len(series)) if cover > 0 and series else 0.0


def _renewals(series: Sequence[float], held: list[Held], min_utilization: float, bought: float) -> list[dict]:
    expiring = [h for h in held if h.expiring]
    if not expiring:
        return []
    staying = sum(h.od_hourly for h in held if h.whole_span and not h.expiring) + bought
    residual = [max(0.0, v - staying) for v in series]
    d = max(expiring, key=lambda h: h.od_hourly).discount
    chosen, _ = optimise.best(residual, d, min_utilization)
    x_renew = chosen.x if chosen else 0.0
    cover = sum(h.od_hourly for h in expiring)
    action = classify_renewal(x_renew, cover)
    out = []
    for h in expiring:
        share = x_renew * h.od_hourly / cover
        target = round(share * (1 - h.discount), 2) if action in ("renew_more", "renew_less") else None
        if action == "renew_same":
            target = round(h.od_hourly * (1 - h.discount), 2)
        out.append(_entry(h, action, target_hourly_commit=target, renewal_od_hourly=round(x_renew, 4)))
    return out


def _standing(series: Sequence[float], held: list[Held]) -> list[dict]:
    active = [h for h in held if h.whole_span]
    staying = [h for h in active if not h.expiring]
    cover = sum(h.od_hourly for h in active)
    if not staying or cover <= 0:
        return []
    u = portfolio_utilization(series, cover)
    if u >= UNDERUSED_UTILIZATION:
        return [_entry(h, "keep", portfolio_utilization=round(u, 4)) for h in staying]
    hours = len(series)
    unused = cover * hours - sum(min(v, cover) for v in series)
    d = max(staying, key=lambda h: h.od_hourly).discount
    wasted = round(unused * (1 - d) / hours * HOURS_PER_MONTH, 2)
    return [_entry(h, "underused", portfolio_utilization=round(u, 4), wasted_per_month=wasted) for h in staying]


def existing(usage: Usage, held: Portfolio, min_utilization: float, bought: Mapping[str, float]) -> list[dict]:
    """`bought`: the new purchase recommended per scope (on-demand equivalent), so a renewal is sized on what
    is left after it and the two never cover the same usage twice."""
    out: list[dict] = []
    for scope in dict.fromkeys(h.scope for h in held.held):
        in_scope = [h for h in held.held if h.scope == scope]
        series = usage.series[scope]
        out += _renewals(series, in_scope, min_utilization, bought.get(scope, 0.0)) + _standing(series, in_scope)
    return out
