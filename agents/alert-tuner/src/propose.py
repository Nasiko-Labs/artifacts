"""Pure: tuning proposals for noisy alerts, computed in code. The model words none of them.

1. lengthen_for     p90 of zero-action durations < 60 min: the smallest bucket >= p90; avoided = zero-action
                    firings that resolved within it.
2. lower_urgency    no acknowledgement in the window and fired >= 2N (text only).
3. threshold_change only with pasted values at firing: ceil to 2 significant digits of the nearest-rank
                    p95 of the values above the current threshold; else needs_metric_data with the format.
4. review_or_remove 100% zero-action and fired >= 4N (text only, owner review first).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from noise import AlertStats, nearest_rank

FOR_BUCKETS_MIN = (5, 10, 15, 30, 60)
P95 = 95
LENGTHEN, URGENCY, THRESHOLD, NEEDS_DATA, REVIEW = (
    "lengthen_for", "lower_urgency", "threshold_change", "needs_metric_data", "review_or_remove")
TEXT_ONLY = frozenset({URGENCY, NEEDS_DATA, REVIEW})
_THRESHOLD_IN_NAME = re.compile(r"(?:>=?|above|over|exceeds?)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def proposal(alert: str, kind: str, change: str, avoided: int | None = None, of: int | None = None) -> dict:
    return {"alert": alert, "kind": kind, "change": change, "avoided": avoided, "of": of, "file": None, "line": None,
            "patch": None, "pr": None}


def for_bucket(p90: float | None) -> int | None:
    if p90 is None or p90 >= FOR_BUCKETS_MIN[-1]:
        return None
    return next(b for b in FOR_BUCKETS_MIN if b >= p90)


def lengthen(s: AlertStats) -> dict | None:
    minutes = for_bucket(s.p90_duration_min)
    if minutes is None:
        return None
    avoided = sum(1 for d in s.zero_durations if d <= minutes)
    return proposal(s.alert, LENGTHEN, f"lengthen `for` to {minutes}m", avoided, len(s.zero_durations))


def ceil_sig(value: float, digits: int = 2) -> float:
    if value == 0:
        return 0.0
    exponent = math.floor(math.log10(abs(value))) - (digits - 1)
    factor = 10.0 ** exponent
    result = round(math.ceil(round(value / factor, 9)) * factor, 9)
    return float(int(result)) if result == int(result) else result


def current_threshold(alert: str) -> float | None:
    found = _THRESHOLD_IN_NAME.search(alert or "")
    return float(found.group(1)) if found else None


def threshold(alert: str, values: Sequence[float], current: float | None = None) -> dict:
    current = current if current is not None else current_threshold(alert)
    above = [v for v in values if current is None or v > current]
    if not above:
        return needs_data(alert)
    proposed = ceil_sig(nearest_rank(above, P95))
    avoided = sum(1 for v in above if v <= proposed)
    old = f"from {fmt(current)} " if current is not None else ""
    return proposal(alert, THRESHOLD, f"raise the threshold {old}to {fmt(proposed)}", avoided, len(above))


def needs_data(alert: str) -> dict:
    return proposal(alert, NEEDS_DATA, f'paste "{alert}: <value at each firing>, ..." to get a threshold proposal')


def for_alert(s: AlertStats, min_firings: int, values: Sequence[float] = ()) -> list[dict]:
    out: list[dict] = []
    longer = lengthen(s)
    if longer:
        out.append(longer)
    if values:
        out.append(threshold(s.alert, values))
    elif longer is None:
        out.append(needs_data(s.alert))
    if s.ack_count == 0 and s.fired >= 2 * min_firings:
        out.append(proposal(s.alert, URGENCY, "route to low urgency (notify only, no page)"))
    if s.zero_action_pct == 100 and s.fired >= 4 * min_firings:
        out.append(proposal(s.alert, REVIEW, "review with the owner whether this alert can be removed"))
    return out


def for_all(noisy: Sequence[AlertStats], min_firings: int, pasted: Mapping[str, Sequence[float]]) -> list[dict]:
    """Every proposal, ordered by estimated pages avoided (then alert order)."""
    out = [p for s in noisy for p in for_alert(s, min_firings, values_for(s.alert, pasted))]
    return sorted(out, key=lambda p: -(p["avoided"] or 0))


def values_for(alert: str, pasted: Mapping[str, Sequence[float]]) -> Sequence[float]:
    """Pasted values whose name shares a leading word with the alert ("Disk alert" ~ "Disk usage > 80%")."""
    words = set(re.findall(r"[a-z]{3,}", alert.casefold()))
    for name, values in pasted.items():
        first = re.findall(r"[a-z]{3,}", name.casefold())[:1]
        if name.casefold() == alert.casefold() or (first and first[0] in words):
            return values
    return ()


def fmt(value: float | None) -> str:
    if value is None:
        return "unknown"
    return str(int(value)) if float(value) == int(value) else f"{value:g}"
