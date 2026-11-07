"""Pure: the bias checks, shown as flags under the draft. They never change meaning; removals are reported.

    recency skew   > 60% of usable dated evidence in the last 25% of the period
    one-sided      > 85% one valence, 6+ usable items
    source skew    > 80% of usable evidence from Self, or from one peer
    loaded words   trait words inside used evidence (only the behavior is used)
    parity         strengths vs growth statements, reported (a note, no decision)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta

import lexicons
from evidence import Evidence
from verify import Statement

RECENT_SHARE = 0.60
RECENT_WINDOW = 0.25
MIN_DATED = 4
ONE_SIDED_SHARE = 0.85
ONE_SIDED_MIN = 6
SOURCE_SHARE = 0.80
SOURCE_MIN = 5
DAYS_PER_MONTH = 30.44


def flag(kind: str, text: str, is_note: bool = False) -> dict:
    return {"kind": kind, "text": text, "note": is_note}


def period_of(stated: tuple[date, date] | None, items: Sequence[Evidence]) -> tuple[date, date] | None:
    if stated:
        return stated
    days = sorted(d for d in (i.day for i in items) if d)
    return (days[0], days[-1]) if len(days) >= 2 and days[0] < days[-1] else None


def recency(items: Sequence[Evidence], period: tuple[date, date] | None) -> dict | None:
    dated = [i.day for i in items if i.day]
    if not period or len(dated) < MIN_DATED:
        return None
    start, end = period
    cutoff = start + timedelta(days=(end - start).days * (1 - RECENT_WINDOW))
    recent = sum(1 for d in dated if d >= cutoff)
    if recent / len(dated) <= RECENT_SHARE:
        return None
    months = max(1, int((cutoff - start).days / DAYS_PER_MONTH))
    return flag("recency_skew", f"Mostly recent examples: {recent} of {len(dated)} dated items are from "
                                f"{cutoff.isoformat()} on; the first {months} months are thin.")


def one_sided(items: Sequence[Evidence]) -> dict | None:
    if len(items) < ONE_SIDED_MIN:
        return None
    counts = Counter(i.valence for i in items)
    for valence in ("positive", "negative"):
        if counts[valence] / len(items) > ONE_SIDED_SHARE:
            return flag("one_sided", f"Evidence is one-sided ({counts[valence]} of {len(items)} items {valence}): "
                                     "check for missing counter-examples.")
    return None


def source_skew(items: Sequence[Evidence]) -> dict | None:
    if len(items) < SOURCE_MIN:
        return None
    keys = Counter("self" if i.source == "self" else f"peer:{i.author.casefold()}" if i.source == "peer" and i.author
                   else i.source for i in items)
    for key, count in keys.items():
        if (key == "self" or key.startswith("peer:")) and count / len(items) > SOURCE_SHARE:
            who = "the person's own self-review" if key == "self" else f"one peer ({key[5:]})"
            return flag("source_skew", f"{count} of {len(items)} items come from {who}: add other sources.")
    return None


def loaded_words(items: Sequence[Evidence]) -> dict | None:
    hits = [(i.id, w) for i in items for w in dict.fromkeys(lexicons.trait_hits(i.text))]
    if not hits:
        return None
    listed = ", ".join(f"{eid} '{w}'" for eid, w in hits)
    return flag("loaded_words", f"Loaded words in the evidence ({listed}): only the behavior described is used.")


def parity(statements: Sequence[Statement]) -> dict:
    s = sum(1 for x in statements if x.section == "strengths")
    g = sum(1 for x in statements if x.section == "growth")
    return flag("parity", f"{s} strength and {g} growth statements.", is_note=True)


def checks(usable: Sequence[Evidence], visible: Sequence[Evidence], statements: Sequence[Statement],
           period: tuple[date, date] | None) -> list[dict]:
    """`visible` = every item not left out (loaded words are flagged even on items too general to use)."""
    found = [recency(usable, period), one_sided(usable), source_skew(usable), loaded_words(visible)]
    return [f for f in found if f] + [parity(statements)]
