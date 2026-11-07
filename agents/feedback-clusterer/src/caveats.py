"""Pure: the caveats line by line, each only when it applies. They say what the numbers rest on."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from metrics import MIN_ITEMS, Dataset, ThemeStats

SMALL_SAMPLE = 20
BELOW_LISTED = 8


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def build(data: Dataset, run: Mapping[str, Any], stats: Sequence[ThemeStats], filtered_from: int = 0,
          focus: str = "") -> list[str]:
    lines = [_read_line(data, run, filtered_from, focus)]
    if data.unattributed:
        lines.append(f"{plural(data.unattributed, 'item')} without a customer; each counts as one unknown customer "
                     "of weight 1.")
    if data.weighting == "tier":
        lines.append("Weighted by tier, no ARR given." if not data.customers_with_arr else
                     f"Weighted by tier: ARR given for only {data.customers_with_arr} of {data.customers} customers.")
    if data.trend_note:
        lines.append(data.trend_note[0].upper() + data.trend_note[1:] + ".")
    else:
        unknown = sum(1 for s in stats if s.shown and s.trend == "unknown")
        if unknown:
            lines.append(f"Trend unknown for {plural(unknown, 'theme')} (fewer than 60% of their items are dated).")
    below = [s for s in stats if not s.shown]
    if below:
        listed = "; ".join(f"{s.name} ({plural(s.count, 'item')}, {plural(s.distinct_customers, 'customer')})"
                           for s in below[:BELOW_LISTED])
        more = f"; and {len(below) - BELOW_LISTED} more" if len(below) > BELOW_LISTED else ""
        lines.append(f"{plural(len(below), 'theme')} below threshold (need {MIN_ITEMS} items or 2 customers): "
                     f"{listed}{more}.")
    lines += _cluster_lines(data, run)
    if data.dominant:
        lines.append(f"One account dominates: {data.dominant} wrote more than 40% of the items.")
    if data.items < SMALL_SAMPLE:
        lines.append(f"Small sample: {data.items} items.")
    if run.get("churn_unchecked"):
        lines.append(f"{run['churn_unchecked']} possible churn mentions could not be checked, so they are not listed.")
    lines += [str(n) for n in run.get("notes") or ()]
    return [line for line in lines if line]


def _read_line(data: Dataset, run: Mapping[str, Any], filtered_from: int, focus: str) -> str:
    extra = []
    if run.get("duplicates"):
        extra.append(f"{run['duplicates']} exact duplicates counted once")
    if run.get("unparsed"):
        extra.append(f"{run['unparsed']} lines skipped as too short or without text")
    read = filtered_from if focus else data.items
    line = f"Read {plural(read, 'item')}" + (f" ({'; '.join(extra)})" if extra else "") + "."
    if focus:
        line += f" Filter: {focus} keeps {data.items}."
    if run.get("discovery_sampled"):
        line += " Themes were found in the 400 items with the highest ARR, then every item was assigned."
    return line


def _cluster_lines(data: Dataset, run: Mapping[str, Any]) -> list[str]:
    lines = []
    unreached = len(run.get("unreached") or ())
    none = data.items - data.clustered - unreached
    if none > 0 and data.items:
        lines.append(f"{plural(none, 'item')} ({round(100 * none / data.items)}%) fit no theme.")
    if unreached:
        lines.append(f"{plural(unreached, 'item')} not clustered: the model call failed or my time limit was reached.")
    return lines
