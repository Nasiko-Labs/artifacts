"""The customers step: the CRM's ARR for the distinct customers of items in qualifying themes (at most 25,
highest item count first). A row counts only for a name that was asked; its ARR only when the raw amount is in
this turn's tool output (parsed in code). Unmatched -> ARR unknown, so the tier weight rule applies."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import prompts
import records
from arr import checked_arr
from deps import TurnDeps
from reader import TurnReader
from records import Item, RawCustomers
from sourcing import SourceRun, read_source
from themes import ThemeStats, customer_key


@dataclass(frozen=True)
class Arrs:
    by_key: Mapping[str, float]
    run: SourceRun = field(default_factory=SourceRun)
    asked: int = 0


def names_to_ask(items: Sequence[Item], stats: Sequence[ThemeStats], limit: int) -> list[str]:
    shown_ids = {i for s in stats if s.shown for i in s.item_ids}
    counts = Counter(customer_key(i.customer) for i in items if i.id in shown_ids and customer_key(i.customer))
    first_name = {}
    for item in items:
        first_name.setdefault(customer_key(item.customer), item.customer)
    return [first_name[key] for key, _ in counts.most_common(limit)]


async def customers_step(deps: TurnDeps, turn: TurnReader, names: Sequence[str]) -> Arrs:
    if not names:
        return Arrs({})
    cfg = deps.config
    run = await read_source(deps, turn, "crm", prompts.customers_system(names),
                            "Find each company and its ARR.", cfg.crm_loop_calls, cfg.crm_tool_calls,
                            records.parser(RawCustomers))
    if run.parsed is None:
        return Arrs({}, run, len(names))
    asked = {customer_key(n) for n in names}
    seen = turn.seen
    found: dict[str, float] = {}
    for row in run.parsed.customers:
        key = customer_key(row.name)
        value = checked_arr(row.arr_raw, seen)
        if key in asked and value is not None:
            found[key] = max(value, found.get(key, 0.0))
    return Arrs(found, run, len(names))


def with_arr(items: Sequence[Item], by_key: Mapping[str, float]) -> tuple[Item, ...]:
    """A customer's ARR is the max seen across the CRM and any pasted amount."""
    out = []
    for item in items:
        crm = by_key.get(customer_key(item.customer))
        known = [a for a in (item.arr, crm) if a is not None]
        out.append(replace(item, arr=max(known)) if known else item)
    return tuple(out)
