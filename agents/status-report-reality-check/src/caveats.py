"""Pure: the caveats line: what was not read, not mapped or not compared, so a partial answer says so."""

from __future__ import annotations

from collections import Counter

import rules
import statuses as st
from match import NONE

MAX_NAMED = 5


def build(items: list[dict], tickets: list[dict], ctx: rules.Ctx, meta: dict) -> list[str]:
    notes = list(meta.get("parse_caveats") or ())
    unknown = Counter(t["status"] or "(empty)" for t in tickets if rules.category(t, ctx) == st.UNKNOWN)
    for status, count in unknown.most_common(MAX_NAMED):
        notes.append(f'Status "{_short(status)}" is not mapped (treated as unknown, {count} ticket'
                     f'{"" if count == 1 else "s"}). Add a line like "Status map: {_short(status).casefold()} = '
                     'in progress" to map it.')
    unread = sum(1 for t in tickets if "updated" in (t.get("unparsed") or ()))
    if unread:
        notes.append(f"{unread} ticket{'' if unread == 1 else 's'} have an unreadable updated date; they were "
                     "left out of the staleness checks.")
    not_compared = [i["item"] for i in items if i["reported"] == st.UNKNOWN and i["match"]["kind"] != NONE]
    if not_compared:
        notes.append("Not compared (no status words I could map): " + ", ".join(not_compared[:MAX_NAMED]) + ".")
    if meta.get("dropped"):
        notes.append(f"{meta['dropped']} report item(s) were left out because their quote isn't in the report.")
    if meta.get("trimmed_from"):
        notes.append(f"Used the {len(tickets):,} most recently updated of {meta['trimmed_from']:,} export rows.")
    if meta.get("coverage"):
        notes.append(meta["coverage"])
    if meta.get("fallback"):
        notes.append("Time ran short, so only explicit ticket references and clear title matches were compared.")
    return notes


def _short(text: str) -> str:
    return " ".join(text.replace('"', "'").split())[:40]
