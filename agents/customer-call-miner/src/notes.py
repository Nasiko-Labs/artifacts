"""Pure: one CRM note per account, built by code from verified items only (no model text): call date and
title, items grouped by kind with their verbatim quotes and moment links."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from records import Account, Call, Item, NoteDraft, mm_ss
from windows import parse_day

_KINDS = (("feature_request", "Feature requests"), ("objection", "Objections"), ("competitor", "Competitors mentioned"),
          ("churn_signal", "Churn signals"))
MAX_DESCRIPTION_BYTES = 2000


def build_notes(items: Sequence[Item], calls: Mapping[str, Call], accounts: Mapping[str, Account],
                week_of: str, skip: frozenset[str] = frozenset()) -> tuple[NoteDraft, ...]:
    """Notes for matched accounts with at least one item, highest ARR first, minus `skip` (already written)."""
    by_account: dict[str, list[Item]] = {}
    for item in items:
        if item.account_id and item.account_id in accounts and item.account_id not in skip:
            by_account.setdefault(item.account_id, []).append(item)
    ranked = sorted(by_account, key=lambda a: (-(accounts[a].arr or 0.0), accounts[a].name.casefold()))
    return tuple(NoteDraft(a, accounts[a].name, accounts[a].url, note_text(by_account[a], calls, week_of))
                 for a in ranked)


def note_text(items: Sequence[Item], calls: Mapping[str, Call], week_of: str) -> str:
    lines = [f"Call insights, week of {week_of}"]
    for call_id in dict.fromkeys(i.call_id for i in items):
        call = calls.get(call_id)
        day = parse_day(call.started_at) if call else None
        title = call.title if call and call.title else "Customer call"
        lines += ["", f"{day.isoformat() if day else 'Date unknown'}: {title}" + (f" ({call.url})" if call and call.url else "")]
        for kind, heading in _KINDS:
            own = [i for i in items if i.call_id == call_id and i.type == kind]
            if own:
                lines.append(f"{heading}:")
                lines += [f'- "{i.quote}" ({i.speaker}, at {mm_ss(i.offset_s)})' for i in own]
    return "\n".join(lines)


def description(text: str) -> str:
    """The first 2000 UTF-8 bytes, cut on a character boundary."""
    data = text.encode("utf-8")[:MAX_DESCRIPTION_BYTES]
    return data.decode("utf-8", errors="ignore")
