"""Pure: the candidate cells shown to the model for each claim. A cell is a candidate only when its row
label, column label or tab shares a word with the claim's label (or quote); cells whose period matches
the claim's period rank higher. At most `MAX_CANDIDATES` per claim; the model may choose only from them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import periods
from records import Cell, Claim

MAX_CANDIDATES = 12
LABEL_WEIGHT = 3
QUOTE_WEIGHT = 1
PERIOD_BONUS = 2

_WORD = re.compile(r"[a-z][a-z0-9]*")
# Period words never make a cell a candidate: "Q3" is in every Q3 column.
_PERIOD_WORD = re.compile(r"q[1-4]|h[12]|fy\d{0,4}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
                          r"july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_STOP = frozenset({
    "the", "and", "for", "of", "in", "on", "at", "to", "by", "a", "an", "is", "was", "are", "were", "our", "we",
    "with", "from", "up", "down", "vs", "per", "total", "this", "that", "year", "quarter", "month", "q", "fy",
})
_SYNONYMS: Mapping[str, str] = {
    "rev": "revenue", "sales": "revenue", "turnover": "revenue", "logo": "customer", "client": "customer",
    "account": "customer", "employee": "headcount", "fte": "headcount", "hc": "headcount", "staff": "headcount",
    "ndr": "nrr", "grr": "retention", "churned": "churn", "spend": "burn", "cash": "cash", "runway": "runway",
    "gm": "margin", "ebitda": "ebitda", "arr": "arr", "mrr": "mrr",
}


def words(text: str) -> frozenset[str]:
    found = set()
    for word in _WORD.findall((text or "").casefold()):
        stem = word[:-1] if len(word) > 3 and word.endswith("s") else word
        if len(stem) < 2 or stem in _STOP or _PERIOD_WORD.fullmatch(word):
            continue
        found.add(_SYNONYMS.get(stem, stem))
    return frozenset(found)


def sole_fit(claim: Claim, cells: Sequence[Cell]) -> Cell | None:
    """The one candidate whose labels share a word with the claim's label and whose period equals the
    claim's; failing that, the one candidate sharing a label word in any period (compare.py then reports a
    period_mismatch, the classic wrong-quarter error); None when neither is unique.
    Used only when the model chose nothing valid for the claim."""
    label = words(claim.label) | (words(claim.quote) - words(claim.value_text))
    wanted = periods.parse(claim.period) if claim.period else None
    same_label = [c for c in cells if label & words(f"{c.row_label} {c.col_label}")]
    same_period = [c for c in same_label if wanted is None or _same_period(wanted, c)]
    for fits in (same_period, same_label):
        if len(fits) == 1:
            return fits[0]
    return None


def _same_period(wanted: periods.Period, cell: Cell) -> bool:
    found = periods.parse(f"{cell.col_label} {cell.row_label}")
    return found is not None and periods.same(wanted, found)


def candidates(claim: Claim, cells: Sequence[Cell]) -> tuple[Cell, ...]:
    label, quote = words(claim.label), words(claim.quote) - words(claim.value_text)
    wanted = periods.parse(claim.period) if claim.period else None
    scored: list[tuple[int, int, Cell]] = []
    for order, cell in enumerate(cells):
        cell_words = words(f"{cell.row_label} {cell.col_label} {cell.tab}")
        overlap = LABEL_WEIGHT * len(label & cell_words) + QUOTE_WEIGHT * len((quote - label) & cell_words)
        if not overlap:
            continue
        found = periods.parse(f"{cell.col_label} {cell.row_label}")
        bonus = PERIOD_BONUS if wanted and found and periods.same(wanted, found) else 0
        scored.append((overlap + bonus, order, cell))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return tuple(cell for _, _, cell in scored[:MAX_CANDIDATES])
