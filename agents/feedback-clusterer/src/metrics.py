"""Pure: every number in the reply. Counts, distinct customers, the weighted share, the trend, the
guardrail and the next step are computed here from parsed items and the item -> theme map; the model's
answers carry no number at all."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from fields import TIER_WEIGHTS
from items import FeedbackItem

MIN_ITEMS, MIN_CUSTOMERS = 3, 2
TREND_MIN_DATED_SHARE, TREND_MIN_RANGE_DAYS = 0.6, 14
DOMINANT_SHARE = 0.4
UNATTRIBUTED_WEIGHT = 1.0
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
SORT_KEYS = ("weight", "count", "customers")


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
    dominant: str = ""              # a customer with > 40% of the items
    trend_note: str = ""            # why trends are unknown for the whole dataset, if they are
    top_quartile_arr: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Customer:
    name: str
    arr: float | None
    tier: str


def customers_of(items: Sequence[FeedbackItem]) -> dict[str, _Customer]:
    found: dict[str, _Customer] = {}
    for item in items:
        key = item.customer_key
        if not key:
            continue
        prior = found.get(key)
        arrs = [a for a in (prior.arr if prior else None, item.arr) if a is not None]
        tier = prior.tier if prior and prior.tier != "unknown" else item.tier
        found[key] = _Customer(prior.name if prior else item.customer, max(arrs) if arrs else None, tier)
    return found


def compute(items: Sequence[FeedbackItem], themes: Sequence[dict],
            theme_of: dict[str, str]) -> tuple[list[ThemeStats], Dataset]:
    customers = customers_of(items)
    with_arr = sum(1 for c in customers.values() if c.arr is not None)
    weighting = "arr" if customers and with_arr * 2 >= len(customers) else "tier"

    def weight(c: _Customer) -> float:
        return c.arr if weighting == "arr" and c.arr is not None else TIER_WEIGHTS.get(c.tier, 1.0)

    unattributed = sum(1 for i in items if not i.customer_key)
    total = sum(weight(c) for c in customers.values()) + unattributed * UNATTRIBUTED_WEIGHT
    halves, trend_note = _halves(items)
    stats = []
    for theme in themes:
        members = [i for i in items if theme_of.get(i.id) == theme["id"]]
        if not members:
            continue
        keys = list(dict.fromkeys(i.customer_key for i in members if i.customer_key))
        loose = sum(1 for i in members if not i.customer_key)
        share = (sum(weight(customers[k]) for k in keys) + loose * UNATTRIBUTED_WEIGHT) / total * 100 if total else 0.0
        stats.append(ThemeStats(theme["id"], theme["name"], theme["type"], len(members),
                                tuple(customers[k].name for k in keys), loose, round(min(share, 100.0), 1),
                                trend(members, halves), tuple(i.id for i in members)))
    clustered = sum(1 for i in items if i.id in theme_of)
    data = Dataset(len(items), len(customers), with_arr, weighting, unattributed, clustered,
                   _dominant(items, customers), trend_note, _top_quartile([c.arr for c in customers.values()]))
    return order(stats), data


def order(stats: Sequence[ThemeStats], by: str = "weight") -> list[ThemeStats]:
    keys = {"weight": lambda s: (-s.weighted_score, -s.count, -s.distinct_customers, s.name.casefold()),
            "count": lambda s: (-s.count, -s.weighted_score, -s.distinct_customers, s.name.casefold()),
            "customers": lambda s: (-s.distinct_customers, -s.weighted_score, -s.count, s.name.casefold())}
    return sorted(stats, key=keys.get(by, keys["weight"]))


def _halves(items: Sequence[FeedbackItem]) -> tuple[date | None, str]:
    """The midpoint of the dataset's date range, or (None, why trends are unknown)."""
    dated = sorted(date.fromisoformat(i.date) for i in items if i.date)
    if not dated:
        return None, "trend unknown: no dates given"
    span = (dated[-1] - dated[0]).days
    if span < TREND_MIN_RANGE_DAYS:
        return None, f"trend unknown: the dates span only {span} days"
    return dated[0] + timedelta(days=span / 2), ""


def trend(members: Sequence[FeedbackItem], midpoint: date | None) -> str:
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


def _dominant(items: Sequence[FeedbackItem], customers: dict[str, _Customer]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        if item.customer_key:
            counts[item.customer_key] = counts.get(item.customer_key, 0) + 1
    top = max(counts.items(), key=lambda kv: kv[1], default=None)
    return customers[top[0]].name if top and items and top[1] > DOMINANT_SHARE * len(items) else ""


def _top_quartile(arrs: Sequence[float | None]) -> float | None:
    known = sorted((a for a in arrs if a is not None), reverse=True)
    return known[max(1, len(known) // 4) - 1] if known else None
