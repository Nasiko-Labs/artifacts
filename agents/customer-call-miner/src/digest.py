"""Pure: the weekly digest, rendered by code from checked data only: top themes with counts, weighted ARR
and one quote each, a competitor line and the churn count. Capped in length."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import themes as th
from records import Account, Item
from render import money

TOP_THEMES = 5


def digest_text(window_label: str, items: Sequence[Item], groupings: Mapping[str, Mapping[str, str]],
                accounts: Mapping[str, Account], arr_available: bool, max_chars: int) -> str:
    ranked = th.order([t for kind in ("feature_request", "objection")
                       for t in th.build_themes(items, kind, groupings.get(kind, {}), accounts)])
    lines = [f"Customer call digest, {window_label}", ""]
    for n, theme in enumerate(ranked[:TOP_THEMES], 1):
        kind = "request" if theme.kind == "feature_request" else "objection"
        arr = f", weighted ARR {money(theme.weighted_arr)}" if arr_available else ""
        lines.append(f"{n}. {theme.name} ({kind}): {len(theme.accounts)} account(s), {theme.calls} call(s){arr}")
        if theme.quotes:
            q = theme.quotes[0]
            who = accounts[q.account_id].name if q.account_id in accounts else "unmatched account"
            lines.append(f'   "{q.quote}" ({who})')
    competitors = th.competitor_rows(items, accounts)
    if competitors:
        lines += ["", "Competitors mentioned: " + ", ".join(f"{c.name} ({len(c.accounts) or c.calls})" for c in competitors)]
    churn = [i for i in items if i.type == "churn_signal"]
    lines += ["", f"Churn signals: {len(churn)} from {len({i.account_id or i.call_id for i in churn})} account(s)."]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 1].rstrip() + "…"
