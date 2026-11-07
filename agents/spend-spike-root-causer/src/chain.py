"""Pure: trace the spike from service to usage dimension to tag to team, all in code.

Level 1, service: delta_s = mean_W(s) - mean_B(s); share = delta_s / sum of positive deltas. The spike service is
the largest positive delta; others are listed when share >= 0.15.
Level 2, dimension: within that service, for usage_type, region, account (the ones the export has), the value with
the largest positive delta; the first dimension whose top value explains >= 50% wins, else the largest share.
Level 3, tag: inside service (+ dimension value), the first tag key in priority team, owner, service, project,
cost_center with a positive tagged value; untagged_share = delta of rows without that tag / scope delta. No tag
column, or untagged_share >= 50% → the chain stops at "untagged" and the owner is unknown.
Level 4, team: the tag value (searched for repositories by name).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction

import aggregate as agg
from export import TAG_KEYS, Row

OTHER_SERVICE_SHARE = Fraction(15, 100)
DIMENSION_SHARE = Fraction(50, 100)
UNTAGGED_STOP = Fraction(50, 100)
TEAM_KEYS = ("team", "owner", "project")
SUGGESTED_TAG = "team"
DIMENSION_LABEL = {"usage_type": "usage type", "region": "region", "account": "account"}


@dataclass(frozen=True)
class ChainLink:
    level: str          # service | dimension | tag | untagged
    name: str           # "service", "usage type", "tag team", "untagged"
    value: str
    delta_daily: Fraction
    share: Fraction | None
    rows: int

    @property
    def evidence(self) -> str:
        return f"source rows: {self.rows}"


@dataclass(frozen=True)
class Chain:
    links: tuple[ChainLink, ...]
    others: tuple[ChainLink, ...]       # other services with share >= 0.15
    team: str | None                    # tag value, None when untagged or no positive service
    tag_key: str | None
    untagged_share: Fraction | None
    service_tag: str | None             # the `service` tag value inside the scope, a second repo-search term

    @property
    def service(self) -> str | None:
        return next((link.value for link in self.links if link.level == "service"), None)

    @property
    def dimension(self) -> ChainLink | None:
        return next((link for link in self.links if link.level == "dimension"), None)

    @property
    def stopped_untagged(self) -> bool:
        return any(link.level == "untagged" for link in self.links)


def _deltas(rows: Sequence[Row], p: agg.Periods, key: Callable[[Row], str]) -> list[tuple[str, Fraction, int]]:
    by = agg.group(rows, key)
    return [(k, agg.delta(rs, p), len(rs)) for k, rs in sorted(by.items())]


def _share(part: Fraction, whole: Fraction) -> Fraction | None:
    return part / whole if whole > 0 else None


def trace(rows: Sequence[Row], p: agg.Periods, tag_keys: Sequence[str], dimensions: Sequence[str]) -> Chain:
    services = _deltas(rows, p, lambda r: r.service)
    positive = [s for s in services if s[1] > 0]
    total = sum((d for _, d, _ in positive), Fraction(0))
    if not positive:
        return Chain((), (), None, None, None, None)
    ranked = sorted(positive, key=lambda s: (-s[1], s[0]))
    top, top_delta, top_rows = ranked[0]
    first = ChainLink("service", "service", top, top_delta, _share(top_delta, total), top_rows)
    others = tuple(ChainLink("service", "service", s, d, _share(d, total), n) for s, d, n in ranked[1:]
                   if (_share(d, total) or 0) >= OTHER_SERVICE_SHARE)
    scope = [r for r in rows if r.service == top]
    dim = _dimension(scope, p, dimensions, top_delta)
    if dim is not None:
        scope = [r for r in scope if r.dim(dim[0]) == dim[1].value]
    scope_delta = agg.delta(scope, p)
    tag = _tag(scope, p, tag_keys, scope_delta)
    links = (first, *((dim[1],) if dim else ()))
    service_tag = _top_value(scope, p, "service") if "service" in tag_keys else None
    if tag is None:
        stop = ChainLink("untagged", "untagged", f"add a `{SUGGESTED_TAG}` tag", scope_delta, Fraction(1), len(scope))
        return Chain((*links, stop), others, None, None, Fraction(1), service_tag)
    key, link, untagged = tag
    if untagged >= UNTAGGED_STOP:
        stop = ChainLink("untagged", "untagged", f"{untagged * 100:.0f}% untagged on `{key}`", scope_delta,
                         untagged, len(scope))
        return Chain((*links, stop), others, None, key, untagged, service_tag)
    return Chain((*links, link), others, link.value, key, untagged, service_tag)


def _dimension(scope: Sequence[Row], p: agg.Periods, dimensions: Sequence[str],
               service_delta: Fraction) -> tuple[str, ChainLink] | None:
    best: list[tuple[str, ChainLink]] = []
    for dim in dimensions:
        values = [v for v in _deltas(scope, p, lambda r, d=dim: r.dim(d)) if v[0] and v[1] > 0]
        if not values:
            continue
        value, d, n = min(values, key=lambda v: (-v[1], v[0]))
        share = _share(d, service_delta)
        link = ChainLink("dimension", DIMENSION_LABEL[dim], value, d, share, n)
        if share is not None and share >= DIMENSION_SHARE:
            return dim, link
        best.append((dim, link))
    return max(best, key=lambda b: b[1].share or 0) if best else None


def _tag(scope: Sequence[Row], p: agg.Periods, tag_keys: Sequence[str],
         scope_delta: Fraction) -> tuple[str, ChainLink, Fraction] | None:
    for key in (k for k in TAG_KEYS if k in tag_keys):
        values = [v for v in _deltas(scope, p, lambda r, k=key: r.tags.get(k, "")) if v[0] and v[1] > 0]
        if not values:
            continue
        value, d, n = min(values, key=lambda v: (-v[1], v[0]))
        untagged_delta = agg.delta([r for r in scope if not r.tags.get(key)], p)
        untagged = max(Fraction(0), _share(untagged_delta, scope_delta) or Fraction(0))
        return key, ChainLink("tag", f"tag {key}", value, d, _share(d, scope_delta), n), untagged
    return None


def _top_value(scope: Sequence[Row], p: agg.Periods, key: str) -> str | None:
    values = [v for v in _deltas(scope, p, lambda r: r.tags.get(key, "")) if v[0] and v[1] > 0]
    return min(values, key=lambda v: (-v[1], v[0]))[0] if values else None
