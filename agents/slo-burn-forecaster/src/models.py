"""The stored run and the output envelope. `confidence` and `needs_human` are computed here from the input
gates and statuses, never taken from the model.

Envelope = {summary, items[], confidence, needs_human, services[], as_of, coverage[], rejected_rows}; `items` is
`services` in rank order, and each service carries `burn_rate` and `hours_to_exhaust` (the catalog's names)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import buckets
import burn
import rank
from burn import Forecast
from sli import Sample
from slo import Slo

LOW_FLAGS = frozenset({"assumed_uniform_traffic", "insufficient_data", "looks_cumulative"})
MEDIUM_FLAGS = frozenset({"extrapolated", "stale", "no_recent_traffic"})
RELATED_KINDS = frozenset({"error", "incident"})


@dataclass(frozen=True)
class Related:
    kind: str
    title: str
    url: str | None = None
    when: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "url": self.url, "when": self.when}

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> Related:
        return Related(str(raw.get("kind")), str(raw.get("title")), raw.get("url"), raw.get("when"))


@dataclass(frozen=True)
class Run:
    """Everything a follow-up needs, checkpointed per conversation: the rows, the targets, the input gates and
    the corroboration found so far."""

    samples: tuple[Sample, ...]
    slos: Mapping[str, Slo]
    rejected: int = 0
    aggregated: bool = False
    related: Mapping[str, tuple[Related, ...]] = field(default_factory=dict)
    related_notes: tuple[str, ...] = ()

    def series(self) -> dict[str, buckets.Series]:
        return buckets.build(self.samples)

    def services(self) -> list[str]:
        return list(self.series())

    def missing(self) -> list[str]:
        return [s for s in self.services() if s not in self.slos or not self.slos[s].complete]

    def forecasts(self, now: int) -> tuple[Forecast, ...]:
        found = [burn.forecast(series, self.slos[name], now) for name, series in self.series().items()
                 if name in self.slos and self.slos[name].complete]
        return rank.ranked(found)

    def to_dict(self) -> dict:
        return {"samples": [s.to_list() for s in self.samples],
                "slos": {k: v.to_list() for k, v in self.slos.items()}, "rejected": self.rejected,
                "aggregated": self.aggregated,
                "related": {k: [r.to_dict() for r in v] for k, v in self.related.items()},
                "related_notes": list(self.related_notes)}

    @staticmethod
    def from_dict(raw: Mapping[str, Any] | None) -> Run | None:
        if not raw or not raw.get("samples"):
            return None
        return Run(tuple(Sample.from_list(s) for s in raw["samples"]),
                   {k: Slo.from_list(v) for k, v in (raw.get("slos") or {}).items()}, int(raw.get("rejected") or 0),
                   bool(raw.get("aggregated")),
                   {k: tuple(Related.from_dict(r) for r in v) for k, v in (raw.get("related") or {}).items()},
                   tuple(raw.get("related_notes") or ()))


def confidence_of(forecasts: Sequence[Forecast], run: Run) -> str:
    flags = {flag for f in forecasts for flag in f.flags}
    if flags & LOW_FLAGS or not forecasts:
        return "low"
    if flags & MEDIUM_FLAGS or run.rejected or run.aggregated:
        return "medium"
    return "high"


def needs_human(forecasts: Sequence[Forecast]) -> bool:
    return any(f.status in burn.ALERTING for f in forecasts)


def iso(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value: Decimal | None, places: int) -> float | None:
    return None if value is None else float(burn.round_to(value, places))


def service_dict(f: Forecast, share: int, related: Sequence[Related]) -> dict:
    remaining = None if f.remaining_frac is None else f.remaining_frac * 100
    worst = None if f.worst_window is None else {"start": iso(f.worst_window[0]), "end": iso(f.worst_window[1])}
    return {
        "name": f.name, "slo": float(Decimal(f.slo.percent)), "period_days": f.slo.period_days, "status": f.status,
        "burn_rate": {k: _num(v, 2) for k, v in f.burn.items()},
        "burn_rate_current": _num(f.burn_current, 2), "current_window": f.current_window,
        "hours_to_exhaust": _num(f.hours_to_exhaust, 1), "exhaust_at": iso(f.exhaust_at),
        "budget_remaining_pct": _num(remaining, 1), "share_of_errors_pct": share, "worst_window": worst,
        "as_of": iso(f.as_of), "flags": list(f.flags), "related": [r.to_dict() for r in related],
        **({"what_if_factor": float(f.factor)} if f.factor != 1 else {}),
    }


def envelope(forecasts: Sequence[Forecast], run: Run, summary: str, coverage: Sequence[str]) -> dict:
    shares = rank.shares(forecasts)
    services = [service_dict(f, shares.get(f.name, 0), run.related.get(f.name, ())) for f in forecasts]
    as_of = max((f.as_of for f in forecasts), default=None)
    return {"summary": summary, "items": services, "confidence": confidence_of(forecasts, run),
            "needs_human": needs_human(forecasts), "services": services, "as_of": iso(as_of),
            "coverage": list(coverage), "rejected_rows": run.rejected}
