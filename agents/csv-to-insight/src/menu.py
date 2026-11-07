"""Pure: the closed analysis menu, plan validation, the default plan and the chart choice.

The model only NAMES analyses from this menu with column names. Code checks every item (known kind,
existing columns, fitting dtypes, no extra keys, ≤ 5 items) and calls its own function; an invalid item is
dropped and counted, and when none survive the default plan runs instead. Nothing the model returns is
ever executed."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import analyses as a
import tabular as t
from profiling import Profile
from trend import trend

MAX_ANALYSES = 5
MAX_TOP_N = 10
AGGS = ("sum", "mean", "count")
ITEM_KEYS = frozenset({"kind", "columns", "why", "agg", "n"})
NUM, CAT, DATE = ("numeric",), ("category", "boolean"), ("datetime",)
BAR_KINDS = ("top_n", "composition", "compare_groups")
QUALITY_KINDS = frozenset({"missingness", "duplicates_and_keys"})
AGG_ALIASES = {"sum": "sum", "total": "sum", "mean": "mean", "avg": "mean", "average": "mean", "count": "count"}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spec:
    fn: Callable
    roles: Mapping[str, tuple[str, ...]]
    optional: frozenset[str] = frozenset()
    signature: str = ""


MENU: dict[str, Spec] = {
    "summary_stats": Spec(a.summary_stats, {"value": NUM}, signature="summary_stats(value: numeric)"),
    "top_n": Spec(a.top_n, {"group": CAT, "value": NUM}, frozenset({"value"}),
                  "top_n(group: category, value: numeric; n <= 10; agg: sum|mean|count)"),
    "composition": Spec(a.composition, {"group": CAT, "value": NUM},
                        signature="composition(group: category, value: numeric)"),
    "trend": Spec(trend, {"date": DATE, "value": NUM},
                  signature="trend(date: datetime, value: numeric; agg: sum|mean|count)"),
    "compare_groups": Spec(a.compare_groups, {"group": CAT, "value": NUM},
                           signature="compare_groups(group: category, value: numeric)"),
    "correlation": Spec(a.correlation, {"a": NUM, "b": NUM}, signature="correlation(a: numeric, b: numeric)"),
    "outliers": Spec(a.outliers, {"value": NUM}, signature="outliers(value: numeric)"),
    "missingness": Spec(t.missingness, {}, signature="missingness()"),
    "duplicates_and_keys": Spec(t.duplicates_and_keys, {}, signature="duplicates_and_keys()"),
    "distribution": Spec(t.distribution, {"group": CAT}, signature="distribution(group: category)"),
    "cross_tab": Spec(t.cross_tab, {"a": CAT, "b": CAT, "value": NUM}, frozenset({"value"}),
                      "cross_tab(a: category, b: category, value: numeric, optional)"),
}


@dataclass(frozen=True)
class PlanItem:
    kind: str
    columns: dict[str, str] = field(default_factory=dict)
    agg: str = "sum"
    n: int = MAX_TOP_N
    why: str = ""

    def params(self) -> dict:
        return {"agg": self.agg, "n": self.n}

    def key(self) -> tuple:
        return self.kind, tuple(sorted(self.columns.items())), self.agg

    def to_dict(self) -> dict:
        return {"kind": self.kind, "columns": dict(self.columns), "agg": self.agg}


def validate_item(raw: Any, profile: Profile) -> PlanItem | None:
    if not isinstance(raw, Mapping) or set(raw) - ITEM_KEYS:
        return None
    spec = MENU.get(str(raw.get("kind") or ""))
    if spec is None:
        return None
    columns = _roles_of(raw.get("columns") or {}, spec, profile)
    if columns is None:
        return None
    chosen: dict[str, str] = {}
    for role, dtypes in spec.roles.items():
        name = columns.get(role)
        if name in (None, "") and role in spec.optional:
            continue
        column = profile.column(str(name)) if isinstance(name, str) else None
        if column is None or column.dtype not in dtypes:
            return None
        chosen[role] = column.name
    agg = AGG_ALIASES.get(str(raw.get("agg") or "sum").strip().casefold(), "")
    if agg not in AGGS or (agg != "count" and spec.roles.get("value") and "value" not in chosen):
        return None
    n = raw.get("n", MAX_TOP_N)
    n = int(n) if isinstance(n, str) and n.strip().isdigit() else n
    n = n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_TOP_N else MAX_TOP_N
    return PlanItem(str(raw["kind"]), chosen, agg, n, str(raw.get("why") or "")[:120])


def validate_plan(analyses: Any, profile: Profile, limit: int = MAX_ANALYSES) -> tuple[list[PlanItem], int]:
    """(valid items in order, number dropped). Duplicates and items over the limit count as dropped."""
    raw_items = analyses if isinstance(analyses, list) else []
    items: list[PlanItem] = []
    dropped = 0
    for raw in raw_items:
        item = validate_item(raw, profile)
        if item is None or len(items) >= limit or any(i.key() == item.key() for i in items):
            dropped += 1
            shape = sorted(raw.get("columns") or {}) if isinstance(raw, Mapping) and isinstance(
                raw.get("columns"), Mapping) else type(raw).__name__
            log.info("plan item dropped: kind=%s roles=%s", raw.get("kind") if isinstance(raw, Mapping) else "?",
                     shape)
            continue
        items.append(item)
    return items, dropped


def _roles_of(columns: Any, spec: Spec, profile: Profile) -> Mapping | None:
    """The model's role → column map. When it names roles the menu lacks (a dtype name, x/y) or lists bare
    column names, each named column is given to the first open role its dtype fits; the result is checked
    again like any other item."""
    if isinstance(columns, Mapping) and not set(columns) - set(spec.roles):
        return columns
    names = list(columns.values()) if isinstance(columns, Mapping) else columns
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return None
    chosen: dict[str, str] = {}
    for name in names:
        column = profile.column(name)
        role = next((r for r, d in spec.roles.items() if r not in chosen and column and column.dtype in d), None)
        if role is None:
            return None
        chosen[role] = name
    return chosen


def top_up(items: list[PlanItem], profile: Profile, limit: int = MAX_ANALYSES) -> tuple[list[PlanItem], bool]:
    """A plan of only data-quality checks says nothing about the data itself: add the default plan's
    analyses (no duplicates, within the limit). Returns (items, whether any were added)."""
    if not items or any(i.kind not in QUALITY_KINDS for i in items):
        return items, False
    extra = [d for d in default_plan(profile) if all(d.key() != i.key() for i in items)]
    merged = [*items, *extra][:limit]
    return merged, len(merged) > len(items)


def default_plan(profile: Profile) -> list[PlanItem]:
    numeric, dates, cats = profile.of("numeric"), profile.of("datetime"), profile.of("category", "boolean")
    items = [PlanItem("summary_stats", {"value": name}) for name in numeric[:2]]
    if dates and numeric:
        items.append(PlanItem("trend", {"date": dates[0], "value": numeric[0]}))
    elif cats and numeric:
        items.append(PlanItem("top_n", {"group": cats[0], "value": numeric[0]}))
    elif cats:
        items.append(PlanItem("distribution", {"group": cats[0]}))
    return [*items, PlanItem("missingness"), PlanItem("duplicates_and_keys")][:MAX_ANALYSES]


def chart_index(items: list[PlanItem], hint: Any) -> int | None:
    """The model's chart pick when it fits the rule (trend → line; top_n/composition/compare_groups → bar),
    else the first item the rule allows, else None."""
    if isinstance(hint, Mapping):
        idx, kind = hint.get("analysis_index"), hint.get("kind")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(items) \
                and chart_kind(items[idx].kind) == kind and kind != "none":
            return idx
    for i, item in enumerate(items):
        if item.kind == "trend":
            return i
    return next((i for i, item in enumerate(items) if item.kind in BAR_KINDS), None)


def chart_kind(kind: str) -> str:
    return "line" if kind == "trend" else "bar" if kind in BAR_KINDS else "none"


def menu_text() -> str:
    return "\n".join(f"- {spec.signature}" for spec in MENU.values())
