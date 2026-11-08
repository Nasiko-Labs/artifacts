"""Pure: the downside scenario (usage -20%) at the same commitment, and the risk class it implies."""

from __future__ import annotations

from collections.abc import Sequence

import optimise
from constants import DOWNSIDE_FACTOR, EPS, LOW_RISK_MARGIN


def classify(downside_saving: float, downside_utilization: float, d: float) -> str:
    if downside_saving <= EPS:
        return "high"
    return "low" if downside_utilization >= 1 - d + LOW_RISK_MARGIN - EPS else "medium"


def with_downside(sizing: dict, r: Sequence[float]) -> dict:
    if not sizing.get("eligible"):
        return sizing
    d = sizing["discount"]
    level = optimise.evaluate([v * DOWNSIDE_FACTOR for v in r], sizing["od_hourly"], d)
    month = optimise.monthly(level.net_saving, len(r))
    return {**sizing, "downside_monthly_saving": round(month, 2),
            "downside_utilization": round(level.utilization, 4),
            "risk": classify(level.net_saving, level.utilization, d)}
