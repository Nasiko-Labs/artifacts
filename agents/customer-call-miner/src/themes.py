"""Pure: the numbers. Counts, ARR weighting, ordering, quotes per theme, churn urgency, competitor rows.

    weighted_arr(theme) = Σ ARR(a) over the DISTINCT matched accounts a in the theme
    order: weighted ARR desc, then accounts desc, then calls desc
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from checks import normalise
from records import Account, Item

MAX_QUOTES = 3


@dataclass(frozen=True)
class Theme:
    kind: str
    name: str
    mentions: int
    calls: int
    accounts: tuple[str, ...]      # distinct matched account ids
    weighted_arr: float
    unmatched_calls: int
    quotes: tuple[Item, ...]


@dataclass(frozen=True)
class ChurnRow:
    account_id: str | None
    item: Item
    arr: float
    days_to_renewal: int | None


def _arr(account_id: str | None, accounts: Mapping[str, Account]) -> float:
    account = accounts.get(account_id or "")
    return (account.arr or 0.0) if account else 0.0


def theme_of(items: Sequence[Item], kind: str, name: str, accounts: Mapping[str, Account]) -> Theme:
    matched = tuple(dict.fromkeys(i.account_id for i in items if i.account_id))
    unmatched = {i.call_id for i in items if not i.account_id}
    return Theme(kind=kind, name=name, mentions=len(items), calls=len({i.call_id for i in items}),
                 accounts=matched, weighted_arr=sum(_arr(a, accounts) for a in matched),
                 unmatched_calls=len(unmatched), quotes=pick_quotes(items, accounts))


def pick_quotes(items: Sequence[Item], accounts: Mapping[str, Account]) -> tuple[Item, ...]:
    """Up to 3 quotes from distinct accounts, highest ARR first; unmatched calls after."""
    ranked = sorted(items, key=lambda i: (i.account_id is None, -_arr(i.account_id, accounts)))
    picked: list[Item] = []
    seen: set[str] = set()
    for item in ranked:
        key = item.account_id or f"call:{item.call_id}"
        if key not in seen:
            seen.add(key)
            picked.append(item)
    return tuple(picked[:MAX_QUOTES])


def order(themes: Sequence[Theme]) -> list[Theme]:
    return sorted(themes, key=lambda t: (-t.weighted_arr, -len(t.accounts), -t.calls, t.name.casefold()))


def build_themes(items: Sequence[Item], kind: str, grouping: Mapping[str, str],
                 accounts: Mapping[str, Account]) -> list[Theme]:
    groups: dict[str, list[Item]] = {}
    for item in items:
        if item.type == kind:
            groups.setdefault(grouping.get(normalise(item.label), item.label), []).append(item)
    return order([theme_of(group, kind, name, accounts) for name, group in groups.items()])


def competitor_rows(items: Sequence[Item], accounts: Mapping[str, Account]) -> list[Theme]:
    """Grouped by normalised competitor name, no LLM."""
    groups: dict[str, list[Item]] = {}
    names: dict[str, str] = {}
    for item in items:
        if item.type == "competitor" and item.competitor:
            key = normalise(item.competitor)
            names.setdefault(key, item.competitor)
            groups.setdefault(key, []).append(item)
    return order([theme_of(group, "competitor", names[key], accounts) for key, group in groups.items()])


def churn_rows(items: Sequence[Item], accounts: Mapping[str, Account], today: date) -> list[ChurnRow]:
    """One row per churn signal, soonest renewal first (unknown last)."""
    rows = [ChurnRow(i.account_id, i, _arr(i.account_id, accounts), days_to_renewal(i.account_id, accounts, today))
            for i in items if i.type == "churn_signal"]
    return sorted(rows, key=lambda r: (r.days_to_renewal is None, r.days_to_renewal or 0, -r.arr))


def days_to_renewal(account_id: str | None, accounts: Mapping[str, Account], today: date) -> int | None:
    account = accounts.get(account_id or "")
    if account is None or not account.renewal_date:
        return None
    try:
        return (date.fromisoformat(account.renewal_date) - today).days
    except ValueError:
        return None
