"""Pure: is a day's metric unusual against its own trailing baseline? Computed in code, never by the model.

For metric `m` on target day `x` with baseline values `b_1..b_n` (the previous days, excluding `x`, from the
account's first day of data on):
- mean, sample standard deviation `sd` (n - 1), median and MAD (median absolute deviation);
- z = 0.6745 * (x - median) / MAD when MAD > 0, else (x - mean) / sd when sd > 0, else 0 when x == mean and
  +/-inf otherwise;
- flagged when |z| >= 3 AND |x - median| >= the floor: money metrics max(50.00, 2% of current MRR), count metrics
  2 events;
- severity high when |z| >= 5 or the change is >= 10% of current MRR, else medium;
- direction spike (z > 0) or drop; good or bad by metric (churn, contraction, failures up = bad; new, expansion,
  net new up = good).
With fewer than 14 baseline days of data nothing is asserted ("not enough history")."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal

from series import EVENT_METRICS, METRICS, DayMetrics, Series

Z_FLAG = 3.0
Z_HIGH = 5.0
MAD_SCALE = 0.6745
MIN_HISTORY_DAYS = 14
MONEY_FLOOR_MINOR = 5_000
MONEY_FLOOR_SHARE = Decimal("0.02")
HIGH_SHARE = Decimal("0.10")
COUNT_FLOOR = 2
DEFAULT_BASELINE_DAYS = 28
MIN_BASELINE_DAYS = 14
MAX_BASELINE_DAYS = 60

COUNT_METRICS = frozenset({"failed_count"})
GOOD_WHEN_UP = frozenset({"new_mrr", "expansion_mrr", "net_new_mrr"})
LABELS = {"new_mrr": "New MRR", "churned_mrr": "Churned MRR", "expansion_mrr": "Expansion MRR",
          "contraction_mrr": "Contraction MRR", "net_new_mrr": "Net new MRR", "failed_count": "Failed payments",
          "failed_amount": "Failed payment amount"}
SHORT = {"new_mrr": "new", "churned_mrr": "churned", "expansion_mrr": "expansion", "contraction_mrr": "contraction",
         "net_new_mrr": "net new", "failed_count": "failed payments", "failed_amount": "failed amount"}


@dataclass(frozen=True)
class Score:
    mean: float
    sd: float
    median: float
    mad: float
    z: float


@dataclass(frozen=True)
class Anomaly:
    day: str
    currency: str
    metric: str
    direction: str            # spike | drop
    value: int                # minor units, or a count
    median: float
    z: float
    severity: str             # high | medium
    good_or_bad: str          # good | bad

    @property
    def is_money(self) -> bool:
        return self.metric not in COUNT_METRICS

    @property
    def change(self) -> float:
        return self.value - self.median

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> Anomaly:
        return Anomaly(day=str(data["day"]), currency=str(data["currency"]), metric=str(data["metric"]),
                       direction=str(data["direction"]), value=int(data["value"]), median=float(data["median"]),
                       z=float(data["z"]), severity=str(data["severity"]), good_or_bad=str(data["good_or_bad"]))


@dataclass(frozen=True)
class Verdict:
    """One target day in one currency."""

    day: date
    currency: str
    metrics: DayMetrics
    history_days: int
    anomalies: tuple[Anomaly, ...] = ()
    clear: tuple[str, ...] = ()            # metrics checked and not flagged
    not_computed: tuple[str, ...] = ()     # metrics not checked (plan changes unknown)

    @property
    def enough_history(self) -> bool:
        return self.history_days >= MIN_HISTORY_DAYS


def clamp_baseline(days: int | None) -> int:
    if days is None:
        return DEFAULT_BASELINE_DAYS
    return max(MIN_BASELINE_DAYS, min(MAX_BASELINE_DAYS, days))


def score(x: float, baseline: Sequence[float]) -> Score:
    mean = statistics.fmean(baseline)
    sd = statistics.stdev(baseline) if len(baseline) > 1 else 0.0
    median = float(statistics.median(baseline))
    mad = float(statistics.median([abs(b - median) for b in baseline]))
    if mad > 0:
        z = MAD_SCALE * (x - median) / mad
    elif sd > 0:
        z = (x - mean) / sd
    else:
        z = 0.0 if x == mean else math.copysign(math.inf, x - mean)
    return Score(mean, sd, median, mad, z)


def floor_for(metric: str, current_mrr: int | None) -> float:
    if metric in COUNT_METRICS:
        return float(COUNT_FLOOR)
    share = Decimal(current_mrr or 0) * MONEY_FLOOR_SHARE
    return float(max(Decimal(MONEY_FLOOR_MINOR), share))


def is_flagged(z: float, change: float, floor: float) -> bool:
    return abs(z) >= Z_FLAG and abs(change) >= floor


def severity(metric: str, z: float, change: float, current_mrr: int | None) -> str:
    big_share = (metric not in COUNT_METRICS and bool(current_mrr)
                 and Decimal(str(abs(change))) >= Decimal(current_mrr) * HIGH_SHARE)
    return "high" if abs(z) >= Z_HIGH or big_share else "medium"


def good_or_bad(metric: str, direction: str) -> str:
    up_is_good = metric in GOOD_WHEN_UP
    return "good" if (direction == "spike") == up_is_good else "bad"


def baseline_days(series: Series, target: date, length: int) -> tuple[DayMetrics, ...]:
    """The `length` days before `target`, from the account's first day of data on."""
    first = target - timedelta(days=length)
    start = max(first, series.history_start) if series.history_start else first
    return tuple(m for m in series.days if start <= m.day < target)


def check_day(series: Series, target: date, length: int) -> Verdict | None:
    today = series.day(target)
    if today is None:
        return None
    base = baseline_days(series, target, length)
    if len(base) < MIN_HISTORY_DAYS:
        return Verdict(target, series.currency, today, len(base))
    current = today.mrr_end if today.mrr_known else None
    found: list[Anomaly] = []  # local accumulators, returned as tuples
    clear: list[str] = []
    skipped = [m for m in METRICS if m in EVENT_METRICS and not series.changes_known]
    for metric in (m for m in METRICS if m not in skipped):
        anomaly = _check_metric(series.currency, target, metric, today, base, current)
        found.append(anomaly) if anomaly else clear.append(metric)
    return Verdict(target, series.currency, today, len(base), tuple(found), tuple(clear), tuple(skipped))


def _check_metric(currency: str, target: date, metric: str, today: DayMetrics, base: Sequence[DayMetrics],
                  current: int | None) -> Anomaly | None:
    x = today.value(metric)
    s = score(float(x), [float(b.value(metric)) for b in base])
    change = x - s.median
    if not is_flagged(s.z, change, floor_for(metric, current)):
        return None
    direction = "spike" if s.z > 0 else "drop"
    return Anomaly(day=target.isoformat(), currency=currency, metric=metric, direction=direction, value=x,
                   median=s.median, z=s.z, severity=severity(metric, s.z, change, current),
                   good_or_bad=good_or_bad(metric, direction))
