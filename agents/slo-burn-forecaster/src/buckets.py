"""Pure: samples -> time buckets per service, and prorated window sums. Integer seconds and Decimal counts only.

A sample stamped `t` covers `(t - g, t]`, where `g` is the gap back to the service's previous sample (the first
sample uses the gap to the next one; a lone sample is one hour). So the latest bucket ends exactly at the latest
timestamp, which is what the forecast is "as of", and mixed granularity needs no special case. Duplicate
timestamps for a service are summed (error rates given without counts are averaged). A rate given without
request counts is weighted by bucket length: traffic is assumed uniform, and the service is flagged."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from sli import Sample

HOUR = 3600
UNIFORM_PER_HOUR = Decimal(1000)  # weight per hour for rate-only rows; only ratios of it are ever used


@dataclass(frozen=True)
class Bucket:
    start: int
    end: int
    total: Decimal
    errors: Decimal


@dataclass(frozen=True)
class Series:
    service: str
    buckets: tuple[Bucket, ...]
    assumed_uniform: bool = False
    looks_cumulative: bool = False

    @property
    def first(self) -> int:
        return self.buckets[0].start

    @property
    def last(self) -> int:
        return self.buckets[-1].end

    @property
    def span_seconds(self) -> int:
        return self.last - self.first


def build(samples: Iterable[Sample]) -> dict[str, Series]:
    """Service -> its Series, buckets in time order."""
    by_service: dict[str, list[Sample]] = {}  # local accumulator; inputs are never changed
    for sample in samples:
        by_service.setdefault(sample.service, []).append(sample)
    return {name: _series(name, rows) for name, rows in sorted(by_service.items())}


def _series(service: str, rows: Sequence[Sample]) -> Series:
    merged = _merge(rows)
    stamps = sorted(merged)
    buckets, uniform = [], False
    for index, ts in enumerate(stamps):
        gap = _gap(stamps, index)
        total, errors, rate = merged[ts]
        if total is None:
            uniform = True
            total = UNIFORM_PER_HOUR * gap / HOUR
            errors = rate * total
        buckets.append(Bucket(ts - gap, ts, total, errors))
    return Series(service, tuple(buckets), uniform, _cumulative(buckets))


def _merge(rows: Sequence[Sample]) -> dict[int, tuple[Decimal | None, Decimal | None, Decimal | None]]:
    counts, rates = _accumulate((row.ts, row) for row in rows)
    merged = {ts: (t, e, None) for ts, (t, e) in counts.items()}
    merged.update({ts: (None, None, sum(v, Decimal(0)) / len(v)) for ts, v in rates.items() if ts not in counts})
    return merged


def _accumulate(keyed):
    """Local accumulators: key -> summed (total, errors), key -> rates given without counts."""
    counts: dict = {}
    rates: dict = {}
    for key, row in keyed:
        if row.total is not None and row.errors is not None:
            total, errors = counts.get(key, (Decimal(0), Decimal(0)))
            counts[key] = (total + row.total, errors + row.errors)
        elif row.rate is not None:
            rates.setdefault(key, []).append(row.rate)
    return counts, rates


def _gap(stamps: Sequence[int], index: int) -> int:
    if len(stamps) == 1:
        return HOUR
    if index == 0:
        return stamps[1] - stamps[0]
    return stamps[index] - stamps[index - 1]


def _cumulative(buckets: Sequence[Bucket]) -> bool:
    """Totals that never decrease over many buckets look like running counters, not per-bucket counts."""
    totals = [b.total for b in buckets]
    if len(totals) < 6:
        return False
    return all(b >= a for a, b in pairwise(totals)) and totals[-1] > totals[0] * 3


def window(series: Series, start: int, end: int) -> tuple[Decimal, Decimal]:
    """(total, errors) inside (start, end], a bucket partly inside counted by its overlap share."""
    total, errors = Decimal(0), Decimal(0)
    for bucket in series.buckets:
        overlap = min(bucket.end, end) - max(bucket.start, start)
        if overlap <= 0:
            continue
        length = bucket.end - bucket.start
        share = Decimal(overlap) / Decimal(length)
        total += bucket.total * share
        errors += bucket.errors * share
    return total, errors


def totals(series: Series) -> tuple[Decimal, Decimal]:
    return (sum((b.total for b in series.buckets), Decimal(0)), sum((b.errors for b in series.buckets), Decimal(0)))


def hourly(samples: Iterable[Sample]) -> tuple[Sample, ...]:
    """Counts summed into whole-hour buckets (stamped at the hour's end), for very large pastes."""
    sums, rates = _accumulate(((s.service, -(-s.ts // HOUR) * HOUR), s) for s in samples)
    out = [Sample(svc, ts, t, e) for (svc, ts), (t, e) in sums.items()]
    out += [Sample(svc, ts, rate=sum(v, Decimal(0)) / len(v)) for (svc, ts), v in rates.items() if (svc, ts) not in sums]
    return tuple(sorted(out, key=lambda s: (s.service, s.ts)))


def service_names(series: Mapping[str, Series]) -> list[str]:
    return list(series)
