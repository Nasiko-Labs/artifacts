"""Pure: the caveats line by line, each only when it applies (copied from the method, plus the live additions:
sources not read, caps hit, customers without ARR). They say what the numbers rest on."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from themes import MIN_ITEMS, Dataset, ThemeStats

SMALL_SAMPLE = 20
BELOW_LISTED = 8


@dataclass(frozen=True)
class RunFacts:
    """What happened during the pull, for the caveats and the confidence."""

    read_counts: tuple[tuple[str, int], ...] = ()    # (source, items kept) for every source read
    notes: tuple[str, ...] = ()                       # coverage lines, step-limit notes
    duplicates: int = 0
    unparsed: int = 0
    capped: int = 0
    dropped: int = 0
    extracted: int = 0                                # items the sources returned before checks
    unreached: int = 0
    churn_unchecked: int = 0
    customers_without_arr: int = 0
    crm_read: bool = False
    tracker_read: bool = False
    salvage: bool = False
    cut: bool = False
    default_channel: bool = False
    clamped: bool = False
    truncated: bool = False

    @property
    def sources_read(self) -> tuple[str, ...]:
        return tuple(s for s, _ in self.read_counts if s != "pasted")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def build(data: Dataset, run: RunFacts, stats: Sequence[ThemeStats]) -> list[str]:
    lines = [_read_line(data, run)]
    if data.unattributed:
        lines.append(f"{plural(data.unattributed, 'item')} without a customer; each counts as one unknown customer "
                     "of weight 1.")
    if data.weighting == "tier":
        lines.append("Weighted by tier, no ARR given." if not data.customers_with_arr else
                     f"Weighted by tier: ARR known for only {data.customers_with_arr} of {data.customers} customers.")
    elif run.customers_without_arr:
        lines.append(f"{plural(run.customers_without_arr, 'customer')} without ARR (weighted by tier).")
    if data.trend_note:
        lines.append(data.trend_note[0].upper() + data.trend_note[1:] + ".")
    elif unknown := sum(1 for s in stats if s.shown and s.trend == "unknown"):
        lines.append(f"Trend unknown for {plural(unknown, 'theme')} (fewer than 60% of their items are dated).")
    below = [s for s in stats if not s.shown]
    if below:
        listed = "; ".join(f"{s.name} ({plural(s.count, 'item')}, {plural(s.distinct_customers, 'customer')})"
                           for s in below[:BELOW_LISTED])
        more = f"; and {len(below) - BELOW_LISTED} more" if len(below) > BELOW_LISTED else ""
        lines.append(f"Below the bar ({MIN_ITEMS} items or 2 customers): {listed}{more}.")
    none = data.items - data.clustered - run.unreached
    if none > 0 and data.items:
        lines.append(f"{plural(none, 'item')} ({round(100 * none / data.items)}%) fit no theme.")
    if run.unreached:
        lines.append(f"{plural(run.unreached, 'item')} not clustered: over my per-turn limit or the model call failed.")
    if data.dominant:
        lines.append(f"One account dominates: {data.dominant} wrote more than 40% of the items.")
    if data.items < SMALL_SAMPLE:
        lines.append(f"Small sample: {plural(data.items, 'item')}.")
    if run.churn_unchecked:
        lines.append(f"{run.churn_unchecked} possible churn mentions could not be checked, so they are not listed.")
    if run.capped:
        lines.append(f"Cap reached: {plural(run.capped, 'older item')} not read; narrow the window to see them.")
    if run.default_channel:
        lines.append('No channel named: chat was searched for "feedback".')
    if run.clamped:
        lines.append("The window was clamped to 7-90 days.")
    if run.truncated:
        lines.append("Your message was over 100,000 characters; only the first 100,000 were read.")
    return [line for line in (*lines, *run.notes) if line]


def _read_line(data: Dataset, run: RunFacts) -> str:
    extra = []
    if run.duplicates:
        extra.append(f"{plural(run.duplicates, 'exact duplicate')} counted once")
    if run.unparsed:
        extra.append(f"{plural(run.unparsed, 'pasted block')} skipped as too short")
    if run.dropped:
        extra.append(f"{plural(run.dropped, 'item')} dropped because the tool output did not back them")
    return f"Read {plural(data.items, 'item')}" + (f" ({'; '.join(extra)})" if extra else "") + "."
