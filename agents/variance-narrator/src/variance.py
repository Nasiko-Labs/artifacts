"""Pure: the method. Variances, direction, materiality, ranking, offsets, concentration, reconciliation and
the prior-period line, all in Decimal. Nothing here is ever asked of the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import sign
from fmt import money
from table import Row

F, U, FLAT = "F", "U", "flat"
DEFAULT_THRESHOLD = Decimal(10)
FLAT_SHARE = Decimal("0.005")         # |variance| under 0.5% of budget is "on budget"
MIN_SHARE_OF_TOTAL = Decimal("0.005")  # a % breach must also be 0.5% of total budget
ABSOLUTE_SHARE = Decimal("0.03")      # or the variance alone is 3% of total budget
OFFSET_SHARE = Decimal("0.25")
RECONCILE_TOLERANCE = Decimal("0.005")
TOP_N = 5
CONCENTRATION_N = 3
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Options:
    threshold: Decimal = DEFAULT_THRESHOLD
    sign_mode: str = sign.AUTO
    sign_stated: bool = False
    only: str = ""                    # "U" | "F" | "" (all)


@dataclass(frozen=True)
class Item:
    name: str
    budget: Decimal
    actual: Decimal
    variance_abs: Decimal
    variance_pct: Decimal | None
    direction: str
    kind: str
    material: bool = False
    driver: str = ""
    driver_source: str = "not provided"   # "table" | "message" | "not provided"


@dataclass(frozen=True)
class Totals:
    budget: Decimal
    actual: Decimal
    variance_abs: Decimal
    variance_pct: Decimal | None
    direction: str
    kind: str


@dataclass(frozen=True)
class Analysis:
    items: tuple[Item, ...]
    totals: Totals
    biggest: tuple[Item, ...]
    offsets: tuple[Item, ...]
    unexplained: tuple[Item, ...]
    concentration: Decimal | None
    sign_convention: str
    sign_guessed: bool
    caveats: tuple[str, ...] = ()
    reconciled: bool | None = None        # None: the table has no total row
    prior_change: Decimal | None = None
    options: Options = field(default_factory=Options)


def percent(delta: Decimal, base: Decimal) -> Decimal | None:
    return None if base == 0 else delta / abs(base) * HUNDRED


def direction(delta: Decimal, budget: Decimal, kind: str) -> str:
    if delta == 0 or (budget != 0 and abs(delta) < FLAT_SHARE * abs(budget)):
        return FLAT
    return (F if delta > 0 else U) if kind == sign.REVENUE else (U if delta > 0 else F)


def is_material(item: Item, total_budget: Decimal, threshold: Decimal) -> bool:
    if item.direction == FLAT:
        return False
    size, base = abs(item.variance_abs), abs(total_budget)
    if size >= ABSOLUTE_SHARE * base:
        return True
    return item.variance_pct is not None and abs(item.variance_pct) >= threshold and size >= MIN_SHARE_OF_TOTAL * base


def analyse(rows: Sequence[Row], options: Options, drivers: Mapping[str, tuple[str, str]], symbol: str = "") -> Analysis:
    lines = [r for r in rows if not r.is_total]
    total_row = _grand_total(rows)
    kinds = [sign.kind_of(r.name, options.sign_mode) for r in lines]
    budget = sum((r.budget for r in lines), Decimal(0))
    actual = sum((r.actual for r in lines), Decimal(0))
    total_kind = sign.total_kind(total_row.name if total_row else "", kinds, options.sign_mode)
    totals = Totals(budget, actual, actual - budget, percent(actual - budget, budget),
                    direction(actual - budget, budget, total_kind), total_kind)
    items = tuple(_item(r, k, budget, options.threshold, drivers.get(r.name)) for r, k in zip(lines, kinds))
    ranked = sorted((i for i in items if i.material and (not options.only or i.direction == options.only)),
                    key=lambda i: -abs(i.variance_abs))
    convention, guessed = sign.convention(kinds, options.sign_mode, options.sign_stated,
                                          [r.name for r, k in zip(lines, kinds) if k == sign.REVENUE])
    caveats = _reconcile(total_row, budget, actual, symbol)
    return Analysis(items=items, totals=totals, biggest=tuple(ranked[:TOP_N]),
                    offsets=() if options.only else _offsets(items, totals.direction),
                    unexplained=tuple(i for i in ranked if not i.driver), concentration=_concentration(items),
                    sign_convention=convention, sign_guessed=guessed, caveats=caveats,
                    reconciled=None if total_row is None else not caveats, prior_change=_prior_change(lines, actual),
                    options=options)


def _item(row: Row, kind: str, total_budget: Decimal, threshold: Decimal, driver: tuple[str, str] | None) -> Item:
    delta = row.actual - row.budget
    text, source = driver or ("", "not provided")
    item = Item(row.name, row.budget, row.actual, delta, percent(delta, row.budget),
                direction(delta, row.budget, kind), kind, driver=text, driver_source=source if text else "not provided")
    return Item(**{**item.__dict__, "material": is_material(item, total_budget, threshold)})


def _offsets(items: Sequence[Item], total_direction: str) -> tuple[Item, ...]:
    if total_direction == FLAT or not items:
        return ()
    opposite = sorted((i for i in items if i.direction not in (FLAT, total_direction)), key=lambda i: -abs(i.variance_abs))
    largest = max(abs(i.variance_abs) for i in items)
    if not opposite or sum(abs(i.variance_abs) for i in opposite) < OFFSET_SHARE * largest:
        return ()
    material = tuple(i for i in opposite if i.material)
    return material[:CONCENTRATION_N] or (opposite[0],)


def _concentration(items: Sequence[Item]) -> Decimal | None:
    sizes = sorted((abs(i.variance_abs) for i in items), reverse=True)
    whole = sum(sizes, Decimal(0))
    if whole == 0 or len(sizes) <= CONCENTRATION_N:
        return None
    return sum(sizes[:CONCENTRATION_N], Decimal(0)) / whole * HUNDRED


def _grand_total(rows: Sequence[Row]) -> Row | None:
    totals = [r for r in rows if r.is_total]
    grand = [r for r in totals if "sub" not in r.name.casefold()]
    if grand:
        return grand[-1]
    return totals[0] if len(totals) == 1 else None


def _reconcile(total_row: Row | None, budget: Decimal, actual: Decimal, symbol: str) -> tuple[str, ...]:
    if total_row is None:
        return ()
    caveats = []
    for label, summed, stated in (("budget", budget, total_row.budget), ("actual", actual, total_row.actual)):
        gap = abs(summed - stated)
        if gap > RECONCILE_TOLERANCE * abs(stated) or (stated == 0 and gap):
            caveats.append(f"Line items sum to {money(summed, symbol)} {label}, but the table's "
                           f"\"{total_row.name}\" row says {money(stated, symbol)}.")
    return tuple(caveats)


def _prior_change(lines: Sequence[Row], actual: Decimal) -> Decimal | None:
    if not lines or any(r.prior is None for r in lines):
        return None
    prior = sum((r.prior for r in lines), Decimal(0))
    return percent(actual - prior, prior)
