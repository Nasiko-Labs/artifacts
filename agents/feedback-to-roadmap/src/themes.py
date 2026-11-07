"""Pure: the clustering method's numbers, copied from feedback-clusterer/PLAN.md "Method" (2026-11-05); keep in
sync by copying. Counts, distinct customers, the weighted share, the trend, the guardrail and the next step
are computed here from items and the item -> theme map; the model's answers carry no number."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from records import Item

MIN_ITEMS, MIN_CUSTOMERS = 3, 2
TREND_MIN_DATED_SHARE, TREND_MIN_RANGE_DAYS = 0.6, 14
DOMINANT_SHARE = 0.4
UNATTRIBUTED_WEIGHT = 1.0
TIER_WEIGHTS = {"enterprise": 5.0, "mid-market": 3.0, "smb": 1.0, "free": 0.5, "unknown": 1.0}
_TIER_WORDS = {"enterprise": "enterprise", "ent": "enterprise", "mid-market": "mid-market", "midmarket": "mid-market",
               "mid market": "mid-market", "mm": "mid-market", "smb": "smb", "small business": "smb", "free": "free",
               "trial": "free"}
NEXT_STEPS = {
    "bug": "Reproduce, size the fix, and link the affected customers",
    "feature_request": "Check against the roadmap and talk to the {n} customers",
    "usability": "Review the flow with 2 of the customers",
    "performance": "Get timings from the affected customers",
    "pricing_billing": "Review with finance/sales before replying",
    "docs_onboarding": "Fix the doc or add an in-product hint",
    "integration": "Confirm the integration scope with the customers",
    "praise": "Share with the team",
    "other": "Read the items together",
}
_SUFFIX = re.compile(r"(?:[\s,]+(?:inc|llc|ltd|corp))+\.?$")
_NOT_NAME = re.compile(r"[^\w@.\-& ]+")


def customer_key(raw: object) -> str:
    """`Acme Inc.` and `acme` are one customer: casefold, drop punctuation and inc|llc|ltd|corp."""
    text = " ".join(_NOT_NAME.sub(" ", str(raw or "").casefold()).split()).strip(" .-")
    return _SUFFIX.sub("", text).strip(" .-,")


def normalise_tier(raw: object) -> str:
    text = " ".join(str(raw or "").casefold().replace("_", " ").split())
    return _TIER_WORDS.get(text, "unknown")


def dedupe(items: Sequence[Item]) -> tuple[tuple[Item, ...], int]:
    """Exact duplicates (same normalised customer, same normalised text) count once."""
    seen: set[tuple[str, str]] = set()
    kept: list[Item] = []
    for item in items:
        key = (customer_key(item.customer), " ".join(item.text.casefold().split()))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return tuple(kept), len(items) - len(kept)


@dataclass(frozen=True)
class ThemeStats:
    id: str
    name: str
    type: str
    count: int
    customers: tuple[str, ...]
    unattributed: int
    weighted_score: float
    trend: str
    item_ids: tuple[str, ...]

    @property
    def distinct_customers(self) -> int:
        return len(self.customers)

    @property
    def shown(self) -> bool:
        return self.count >= MIN_ITEMS or self.distinct_customers >= MIN_CUSTOMERS

    @property
    def next_step(self) -> str:
        step = NEXT_STEPS.get(self.type, NEXT_STEPS["other"]).format(n=self.distinct_customers)
        return step + ("; trend is rising: raise priority" if self.trend == "rising" else "")


@dataclass(frozen=True)
class Dataset:
    items: int
    customers: int
    customers_with_arr: int
    weighting: str                  # "arr" | "tier"
    unattributed: int
    clustered: int
    dominant: str = ""
    trend_note: str = ""
    top_quartile_arr: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Customer:
    name: str
    arr: float | None
    tier: str


def customers_of(items: Sequence[Item]) -> dict[str, _Customer]:
    found: dict[str, _Customer] = {}
    for item in items:
        key = customer_key(item.customer)
        if not key:
            continue
        prior = found.get(key)
        arrs = [a for a in (prior.arr if prior else None, item.arr) if a is not None]
        tier = prior.tier if prior and prior.tier != "unknown" else item.tier
        found[key] = _Customer(prior.name if prior else item.customer, max(arrs) if arrs else None, tier)
    return found


def compute(items: Sequence[Item], themes: Sequence[Mapping], theme_of: Mapping[str, str]
            ) -> tuple[list[ThemeStats], Dataset]:
    customers = customers_of(items)
    with_arr = sum(1 for c in customers.values() if c.arr is not None)
    weighting = "arr" if customers and with_arr * 2 >= len(customers) else "tier"

    def weight(c: _Customer) -> float:
        return c.arr if weighting == "arr" and c.arr is not None else TIER_WEIGHTS.get(c.tier, 1.0)

    unattributed = sum(1 for i in items if not customer_key(i.customer))
    total = sum(weight(c) for c in customers.values()) + unattributed * UNATTRIBUTED_WEIGHT
    midpoint, trend_note = _midpoint(items)
    stats = []
    for theme in themes:
        members = [i for i in items if theme_of.get(i.id) == theme["id"]]
        if not members:
            continue
        keys = list(dict.fromkeys(customer_key(i.customer) for i in members if customer_key(i.customer)))
        loose = sum(1 for i in members if not customer_key(i.customer))
        share = (sum(weight(customers[k]) for k in keys) + loose * UNATTRIBUTED_WEIGHT) / total * 100 if total else 0.0
        stats.append(ThemeStats(theme["id"], theme["name"], theme["type"], len(members),
                                tuple(customers[k].name for k in keys), loose, round(min(share, 100.0), 1),
                                trend(members, midpoint), tuple(i.id for i in members)))
    clustered = sum(1 for i in items if i.id in theme_of)
    data = Dataset(len(items), len(customers), with_arr, weighting, unattributed, clustered,
                   _dominant(items, customers), trend_note, _top_quartile([c.arr for c in customers.values()]))
    return order(stats), data


def order(stats: Sequence[ThemeStats], by: str = "weight") -> list[ThemeStats]:
    keys = {"weight": lambda s: (-s.weighted_score, -s.count, -s.distinct_customers, s.name.casefold()),
            "count": lambda s: (-s.count, -s.weighted_score, -s.distinct_customers, s.name.casefold()),
            "customers": lambda s: (-s.distinct_customers, -s.weighted_score, -s.count, s.name.casefold())}
    return sorted(stats, key=keys.get(by, keys["weight"]))


def _midpoint(items: Sequence[Item]) -> tuple[date | None, str]:
    dated = sorted(date.fromisoformat(i.date) for i in items if i.date)
    if not dated:
        return None, "trend unknown: no dates given"
    span = (dated[-1] - dated[0]).days
    if span < TREND_MIN_RANGE_DAYS:
        return None, f"trend unknown: the dates span only {span} days"
    return dated[0] + timedelta(days=span / 2), ""


def trend(members: Sequence[Item], midpoint: date | None) -> str:
    dated = [date.fromisoformat(i.date) for i in members if i.date]
    if midpoint is None or not members or len(dated) < TREND_MIN_DATED_SHARE * len(members):
        return "unknown"
    earlier = sum(1 for d in dated if d <= midpoint)
    later = len(dated) - earlier
    if earlier == 0 and later >= 3:
        return "new"
    if later >= 2 and later >= 1.5 * earlier:
        return "rising"
    if earlier >= 2 and later <= 0.5 * earlier:
        return "falling"
    return "steady"


def _dominant(items: Sequence[Item], customers: Mapping[str, _Customer]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        key = customer_key(item.customer)
        if key:
            counts[key] = counts.get(key, 0) + 1
    top = max(counts.items(), key=lambda kv: kv[1], default=None)
    return customers[top[0]].name if top and items and top[1] > DOMINANT_SHARE * len(items) else ""


def _top_quartile(arrs: Sequence[float | None]) -> float | None:
    known = sorted((a for a in arrs if a is not None), reverse=True)
    return known[max(1, len(known) // 4) - 1] if known else None
