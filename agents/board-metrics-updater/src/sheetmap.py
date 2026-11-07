"""Pure: map a metrics sheet's header row to metric keys by normalised name, and build the one row to
append. Unmatched headers stay blank; numbers are never taken from chat text."""

from __future__ import annotations

import re
from collections.abc import Sequence

import render
from mrr import Block
from render import Report

# (key, default header). The default order is used when the sheet has no header we recognise.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("period", "Period"), ("mrr", "MRR"), ("arr", "ARR"), ("new_mrr", "New MRR"), ("expansion_mrr", "Expansion"),
    ("contraction_mrr", "Contraction"), ("churned_mrr", "Churned"), ("logo_churn_pct", "Logo churn %"),
    ("revenue_churn_pct", "Revenue churn %"), ("nrr_pct", "NRR %"), ("grr_pct", "GRR %"),
    ("customers", "Customers"),
)
DEFAULT_KEYS: tuple[str, ...] = tuple(k for k, _ in COLUMNS)
DEFAULT_HEADER: tuple[str, ...] = tuple(h for _, h in COLUMNS)

_SYNONYMS: dict[str, frozenset[str]] = {
    "period": frozenset({"period", "month", "date", "quarter", "reportingperiod"}),
    "currency": frozenset({"currency", "ccy"}),
    "mrr": frozenset({"mrr", "monthlyrecurringrevenue", "endmrr", "endingmrr", "closingmrr"}),
    "start_mrr": frozenset({"startmrr", "startingmrr", "openingmrr", "beginningmrr"}),
    "arr": frozenset({"arr", "annualrecurringrevenue", "annualizedrevenue", "annualisedrevenue"}),
    "new_mrr": frozenset({"newmrr", "new", "newbusiness", "newrevenue"}),
    "expansion_mrr": frozenset({"expansion", "expansionmrr", "upsell", "upgrades"}),
    "contraction_mrr": frozenset({"contraction", "contractionmrr", "downgrades", "downgrade"}),
    "churned_mrr": frozenset({"churned", "churnedmrr", "churnmrr", "lostmrr"}),
    "logo_churn_pct": frozenset({"logochurn", "customerchurn", "logochurnrate", "customerchurnrate"}),
    "revenue_churn_pct": frozenset({"revenuechurn", "grosschurn", "grossrevenuechurn", "mrrchurn",
                                    "revenuechurnrate"}),
    "nrr_pct": frozenset({"nrr", "netrevenueretention", "ndr", "netdollarretention", "netretention"}),
    "grr_pct": frozenset({"grr", "grossrevenueretention", "grossretention"}),
    "customers": frozenset({"customers", "customercount", "activecustomers", "logos", "payingcustomers"}),
}
_NOISE = re.compile(r"[^a-z0-9]+")
_SUFFIXES = ("percent", "pct", "rate")


def normalise(header: str) -> str:
    text = _NOISE.sub("", (header or "").casefold())
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix) and text[:-len(suffix)] in _ALL:
            return text[:-len(suffix)]
    return text


_ALL = frozenset(n for names in _SYNONYMS.values() for n in names)


def map_header(header: Sequence[str]) -> tuple[str | None, ...]:
    """The metric key for each column, or None for a column left blank."""
    keys: tuple[str | None, ...] = ()
    for name in header:
        norm = normalise(name)
        key = next((k for k, names in _SYNONYMS.items() if norm in names), None)
        keys = (*keys, None if key in keys else key)  # a key fills one column only
    return keys


def has_match(keys: Sequence[str | None]) -> bool:
    return any(k is not None for k in keys)


def cell(key: str | None, block: Block, report: Report) -> str:
    if key is None:
        return ""
    if key == "period":
        return report.label
    if key == "currency":
        return block.currency
    if key == "customers":
        return str(block.customers1)
    if key in ("expansion_mrr", "contraction_mrr") and not block.changes_known:
        return ""
    money = {"mrr": block.mrr1, "start_mrr": block.mrr0, "arr": block.arr, "new_mrr": block.new_mrr,
             "expansion_mrr": block.expansion_mrr, "contraction_mrr": block.contraction_mrr,
             "churned_mrr": block.churned_mrr}
    if key in money:
        return render.plain_money(money[key])
    ratio = {"logo_churn_pct": block.logo_churn_pct, "revenue_churn_pct": block.revenue_churn_pct,
             "nrr_pct": block.nrr_pct, "grr_pct": block.grr_pct}[key]
    return "n/a" if ratio is None else str(ratio)


def row(keys: Sequence[str | None], block: Block, report: Report) -> tuple[str, ...]:
    values = tuple(cell(k, block, report) for k in keys)
    # Trailing blanks add nothing to an appended row.
    end = len(values)
    while end > 0 and values[end - 1] == "":
        end -= 1
    return values[:end]
