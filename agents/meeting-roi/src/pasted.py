"""Pure: the fallback when no calendar can be read. A pasted meeting list (title, day, minutes, invited,
accepted[, times]) becomes series facts scored on RSVP and size only: there are no notes and no chat,
so no cancel is ever proposed from it."""

from __future__ import annotations

from collections.abc import Sequence

from request import PastedRow
from roi import Occ, SeriesFacts
from series import MAX_SERIES, normalise_title


def facts_from_rows(rows: Sequence[PastedRow]) -> tuple[tuple[SeriesFacts, ...], int]:
    """(series facts, series left out by the 25 cap); rows with the same title are one series."""
    grouped: dict[str, list[PastedRow]] = {}
    for row in rows:
        grouped.setdefault(normalise_title(row.title) or row.title, []).append(row)
    facts = [_facts(group) for group in grouped.values()]
    ranked = sorted(facts, key=lambda f: -sum(o.person_hours for o in f.occs))
    return tuple(ranked[:MAX_SERIES]), max(0, len(ranked) - MAX_SERIES)


def _facts(rows: Sequence[PastedRow]) -> SeriesFacts:
    occs = tuple(Occ(start=None, duration_h=r.duration_min / 60, invited=r.invited, accepted=r.accepted)
                 for r in rows for _ in range(r.times))
    days = list(dict.fromkeys(r.day for r in rows))
    return SeriesFacts(title=rows[0].title, occs=occs, weekday=", ".join(days[:3]), cadence="as pasted")
