"""Pure: the report shown in chat. Every number comes from themes.py; every link is built in code from
verified pieces (the call's url, the CRM record's url) and every quote passed the verbatim check."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import themes as th
from records import Account, Call, Item, mm_ss

_HEADINGS = (("feature_request", "Feature requests"), ("objection", "Objections"))
MAX_THEMES_SHOWN = 10
NOTHING_BACKED = ("No feature requests, objections, competitor mentions or churn signals could be backed by a "
                  "verbatim customer quote.")


@dataclass(frozen=True)
class Report:
    """Everything the report needs, already checked."""

    window_label: str
    calls: Mapping[str, Call]
    accounts: Mapping[str, Account]
    items: Sequence[Item]
    groupings: Mapping[str, Mapping[str, str]]
    analysed: int
    total_calls: int
    arr_available: bool
    notes: Sequence[str] = ()


def money(value: float | None) -> str:
    return "not available" if value is None else f"{value:,.0f}"


def moment(item: Item, calls: Mapping[str, Call]) -> str:
    """A link to the call, with the moment as text (no timestamp url parameter is assumed)."""
    call = calls.get(item.call_id)
    title = (call.title if call and call.title else "call") + f" at {mm_ss(item.offset_s)}"
    return f"[{title}]({call.url})" if call and call.url else title


def account_ref(account_id: str | None, accounts: Mapping[str, Account]) -> str:
    account = accounts.get(account_id or "")
    if account is None:
        return "unmatched account"
    return f"[{account.name}]({account.url})" if account.url else account.name


def quote_line(item: Item, report: Report) -> str:
    return f'   - "{item.quote}" ({item.speaker}, {account_ref(item.account_id, report.accounts)}, {moment(item, report.calls)})'


def theme_block(n: int, theme: th.Theme, report: Report) -> list[str]:
    arr = money(theme.weighted_arr) if report.arr_available else "not available"
    extra = f" (+ {theme.unmatched_calls} unmatched call{'s' if theme.unmatched_calls != 1 else ''})" \
        if theme.unmatched_calls else ""
    head = (f"{n}. **{theme.name}**: {len(theme.accounts)} account(s), {theme.calls} call(s), "
            f"{theme.mentions} mention(s), weighted ARR {arr}{extra}")
    return [head, *(quote_line(q, report) for q in theme.quotes)]


def render_report(report: Report, today: date) -> str:
    head = (f"**Customer calls, {report.window_label}**: analysed {report.analysed} of {report.total_calls} "
            "external call(s).")
    lines = [head]
    for kind, heading in _HEADINGS:
        found = th.build_themes(report.items, kind, report.groupings.get(kind, {}), report.accounts)
        lines += ["", f"### {heading}"] if found else []
        for n, theme in enumerate(found[:MAX_THEMES_SHOWN], 1):
            lines += theme_block(n, theme, report)
    lines += _competitors(report)
    lines += _churn(report, today)
    if not report.items:
        lines += ["", NOTHING_BACKED]
    if report.notes:
        lines += ["", *(f"- {note}" for note in report.notes)]
    return "\n".join(lines)


def _competitors(report: Report) -> list[str]:
    rows = th.competitor_rows(report.items, report.accounts)
    if not rows:
        return []
    out = ["", "### Competitors", "| Competitor | Accounts | Calls | ARR of those accounts |", "|---|---|---|---|"]
    for row in rows:
        arr = money(row.weighted_arr) if report.arr_available else "not available"
        out.append(f"| {row.name} | {len(row.accounts)} | {row.calls} | {arr} |")
    return out


def _churn(report: Report, today: date) -> list[str]:
    rows = th.churn_rows(report.items, report.accounts, today)
    if not rows:
        return []
    out = ["", "### Churn signals"]
    for row in rows:
        renewal = "renewal date unknown" if row.days_to_renewal is None else f"renews in {row.days_to_renewal} day(s)"
        arr = money(row.arr) if report.arr_available and row.account_id else "not available"
        out.append(f'- {account_ref(row.account_id, report.accounts)} ({renewal}, ARR at risk {arr}): '
                   f'"{row.item.quote}" ({row.item.speaker}, {moment(row.item, report.calls)})')
    return out
