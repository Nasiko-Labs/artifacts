"""Pure: days far off their trailing mean, per service and for the total.

A day `d` with at least 7 prior points is compared with the previous 14 days (all prior if fewer): it is
anomalous when std > 0, |x − mean| > 2σ (population std) AND |x − mean| ≥ max($5, 10% of mean). Consecutive
anomalous days of one series merge into one episode (peak = the largest |x − mean|). At most 10 episodes,
ranked by that deviation. Monthly data is skipped."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from stats import gross

MIN_PRIOR = 7
TRAILING = 14
SIGMAS = 2.0
MIN_ABS = 5.0
MIN_REL = 0.10
MAX_EPISODES = 10
TOTAL = "Total"
MONTHLY_NOTE = "Anomalies need daily data."


@dataclass(frozen=True)
class Anomaly:
    date_start: str
    date_end: str
    service: str
    value: float
    mean: float
    std: float
    sigma: float
    direction: str

    @property
    def deviation(self) -> float:
        return abs(self.value - self.mean)

    def to_dict(self) -> dict:
        return {"date_start": self.date_start, "date_end": self.date_end, "service": self.service,
                "value": self.value, "mean": self.mean, "std": self.std, "sigma": self.sigma,
                "direction": self.direction}


def daily_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Gross cost per day (rows, every day of the span, missing = 0) and service (columns)."""
    g = gross(df)
    if g.empty:
        return pd.DataFrame()
    pivot = g.pivot_table(index="date", columns="service", values="cost", aggfunc="sum", fill_value=0.0)
    full = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    return pivot.reindex(full, fill_value=0.0)


def find(df: pd.DataFrame) -> list[Anomaly]:
    matrix = daily_matrix(df)
    if matrix.empty:
        return []
    episodes: list[Anomaly] = []
    series = {str(c): matrix[c] for c in matrix.columns}
    series[TOTAL] = matrix.sum(axis=1)
    for name, values in series.items():
        episodes.extend(_episodes(name, values))
    episodes.sort(key=lambda a: (-a.deviation, a.date_start, a.service))
    return episodes[:MAX_EPISODES]


def flags(values: pd.Series) -> list[tuple[pd.Timestamp, float, float, float]]:
    """(day, value, mean, std) for every anomalous day of one series."""
    arr = values.to_numpy(dtype=float)
    out = []
    for i in range(MIN_PRIOR, len(arr)):
        window = arr[max(0, i - TRAILING):i]
        mean, std = float(np.mean(window)), float(np.std(window))
        dev = abs(arr[i] - mean)
        if std > 0 and dev > SIGMAS * std and dev >= max(MIN_ABS, MIN_REL * mean):
            out.append((values.index[i], float(arr[i]), mean, std))
    return out


def _episodes(name: str, values: pd.Series) -> list[Anomaly]:
    days = flags(values)
    merged: list[list[tuple]] = []
    for day in days:
        if merged and (day[0] - merged[-1][-1][0]).days == 1:
            merged[-1].append(day)
        else:
            merged.append([day])
    result = []
    for run in merged:
        peak = max(run, key=lambda d: abs(d[1] - d[2]))
        _, value, mean, std = peak
        result.append(Anomaly(run[0][0].strftime("%Y-%m-%d"), run[-1][0].strftime("%Y-%m-%d"), name, value, mean,
                              std, (value - mean) / std, "up" if value > mean else "down"))
    return result
