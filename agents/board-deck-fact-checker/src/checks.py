"""Pure: the code checks on what the model returns, and the in-deck consistency check.

- A claim is kept only when its quote appears verbatim in that slide's text, its value appears inside the
  quote, the value parses as a number, and it is not a date, year, quarter, page, version, phone or list
  number. Duplicates (same slide, quote and value) collapse; at most `MAX_CLAIMS` per run (stated).
- A match is kept only when every cell id (and formula operand) is in that claim's shortlist.
- `inconsistencies` finds the same label and period stated with different values on different slides.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import figures
import periods
import shortlist
from records import KINDS, OPS, Claim, Match
from slides import Slide

MAX_CLAIMS = 150
MAX_CELLS_PER_MATCH = 2
# Which claim kinds each formula may serve: a plain figure ("ARR of $12.4M") is never a growth rate.
FORMULA_KINDS = {"growth": ("growth", "percent"), "ratio": ("ratio", "percent"),
                 "sum": ("absolute", "count"), "difference": ("growth", "absolute", "count", "percent")}
_SPACE = re.compile(r"\s+")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_VERSION = re.compile(r"^v?\d+(?:\.\d+){2,}$|^v\d", re.IGNORECASE)
_LIST_NUMBER = re.compile(r"^\d{1,2}[.)]$")
_PREFIXES = re.compile(r"(?:\b(?:q|h|fy|slide|page|p\.|pg|v|version|step|phase|round|series)\s*#?|#)\s*$",
                       re.IGNORECASE)


def normal(text: str) -> str:
    return _SPACE.sub(" ", (text or "").replace(" ", " ")).strip().casefold()


def parse_answer(text: str) -> dict | None:
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def items(answer: Mapping[str, Any] | None, key: str) -> list[Mapping[str, Any]]:
    raw = (answer or {}).get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


# --- claims ---------------------------------------------------------------------------------------------

def is_not_claim(value_text: str, quote: str) -> bool:
    """Dates, years, quarters, page/slide/version numbers, phone numbers and list numbering."""
    value = value_text.strip()
    number = figures.parse(value, whole=True)
    if number is None or _LIST_NUMBER.match(value) or _VERSION.match(value) or _PHONE.fullmatch(value):
        return True
    plain = number.unit == "count" and number.scale == 1 and "," not in value
    if plain and number.precision == 1 and 1900 <= abs(number.value) <= 2100:
        return True  # a year standing alone
    at = quote.find(value)
    before = quote[:at] if at >= 0 else ""
    return plain and bool(_PREFIXES.search(before))


@dataclass(frozen=True)
class ClaimCheck:
    claims: tuple[Claim, ...]
    dropped: int = 0
    capped: int = 0


def check_claims(raw: Sequence[Mapping[str, Any]], deck_slides: Sequence[Slide], start: int = 0) -> ClaimCheck:
    by_n: dict[int, str] = {}
    for slide in deck_slides:
        by_n[slide.n] = f"{by_n.get(slide.n, '')}\n{normal(slide.text)}"
    kept: list[Claim] = []
    keys: set[tuple[int, str, str]] = set()
    dropped = 0
    for item in raw:
        claim = _claim(item, by_n, f"k{start + len(kept) + 1}")
        if claim is None:
            dropped += 1
            continue
        key = (claim.slide, normal(claim.quote), normal(claim.value_text))
        if key in keys:
            continue
        keys.add(key)
        kept.append(claim)
    return ClaimCheck(tuple(kept), dropped)


def _claim(item: Mapping[str, Any], by_n: Mapping[int, str], claim_id: str) -> Claim | None:
    quote, value = text_of(item.get("quote")), text_of(item.get("value_text"))
    slide = item.get("slide")
    slide = int(slide) if isinstance(slide, (int, float)) or (isinstance(slide, str) and slide.isdigit()) else None
    if slide is None or slide not in by_n or not quote or not value:
        return None
    if normal(quote) not in by_n[slide] or normal(value) not in normal(quote) or is_not_claim(value, quote):
        return None
    kind = text_of(item.get("kind")).casefold()
    number = figures.parse(value, whole=True)
    if kind not in KINDS:
        kind = {"percent": "percent", "ratio": "ratio"}.get(number.unit, "absolute")
    basis = text_of(item.get("basis")).casefold() or None
    return Claim(claim_id=claim_id, slide=slide, quote=quote, label=text_of(item.get("label")) or quote[:60],
                 value_text=value, period=text_of(item.get("period")), kind=kind,
                 basis=basis if basis in ("relative", "points") else None)


def cap(claims: Sequence[Claim]) -> ClaimCheck:
    kept = tuple(claims[:MAX_CLAIMS])
    return ClaimCheck(kept, 0, max(0, len(claims) - MAX_CLAIMS))


def envelope_claims(raw: Sequence[Mapping[str, Any]]) -> tuple[Claim, ...]:
    """Claims from an earlier run's envelope: re-checked as given (the deck itself is not re-read)."""
    kept: list[Claim] = []
    for item in raw:
        quote, value = text_of(item.get("claim_quote") or item.get("quote")), text_of(item.get("value_text")
                                                                                       or item.get("deck_value"))
        slide = item.get("slide")
        if not isinstance(slide, int) or not quote or not value or normal(value) not in normal(quote):
            continue
        if figures.parse(value, whole=True) is None:
            continue
        kind = text_of(item.get("kind"))
        kept.append(Claim(f"k{len(kept) + 1}", slide, quote, text_of(item.get("label")) or quote[:60], value,
                          text_of(item.get("period")), kind if kind in KINDS else "absolute", None))
    return tuple(kept[:MAX_CLAIMS])


# --- matches --------------------------------------------------------------------------------------------

def check_match(item: Mapping[str, Any] | None, claim: Claim, allowed: frozenset[str]) -> Match:
    if item is None:
        return Match(claim.claim_id, note="no matching cell in the source")
    ids = [c for c in (item.get("cell_ids") or []) if isinstance(c, str)]
    formula = item.get("formula") if isinstance(item.get("formula"), Mapping) else None
    reason = text_of(item.get("reason"))[:200]
    fits = formula is not None and claim.kind in FORMULA_KINDS.get(text_of(formula.get("op")), ())
    if formula and (fits or not ids):  # a misfit formula next to a cell choice: the cell is used
        return _formula(claim, formula, allowed, reason)
    if not ids:
        return Match(claim.claim_id, note="no matching cell in the source")
    if any(c not in allowed for c in ids):
        return Match(claim.claim_id, note="the proposed cell was not among the candidates")
    return Match(claim.claim_id, cell_ids=tuple(dict.fromkeys(ids))[:MAX_CELLS_PER_MATCH], reason=reason)


def _formula(claim: Claim, formula: Mapping[str, Any], allowed: frozenset[str], reason: str) -> Match:
    op = text_of(formula.get("op"))
    operands = [o for o in (formula.get("operands") or []) if isinstance(o, str)]
    if op not in OPS or len(operands) < 2 or any(o not in allowed for o in operands):
        return Match(claim.claim_id, note="the proposed calculation used cells outside the candidates")
    if claim.kind not in FORMULA_KINDS[op]:
        return Match(claim.claim_id, note=f"a {op} calculation does not fit a {claim.kind} figure")
    return Match(claim.claim_id, op=op, operands=tuple(operands), reason=reason)


# --- in-deck consistency --------------------------------------------------------------------------------

def inconsistencies(claims: Sequence[Claim]) -> tuple[dict, ...]:
    groups: dict[tuple[frozenset[str], str], list[Claim]] = {}
    for claim in claims:
        period = periods.parse(claim.period) if claim.period else None
        label = shortlist.words(claim.label)
        if period is None or not label:
            continue
        groups.setdefault((label, period.label()), []).append(claim)
    found: list[dict] = []
    for (_, period), group in groups.items():
        slides_seen = {c.slide for c in group}
        if len(slides_seen) < 2 or not _differ(group):
            continue
        found.append({"label": group[0].label, "period": period,
                      "statements": [{"slide": c.slide, "quote": c.quote, "value": c.value_text} for c in group]})
    return tuple(sorted(found, key=lambda f: f["statements"][0]["slide"]))


def _differ(group: Sequence[Claim]) -> bool:
    numbers = [c.number for c in group]
    if len({n.unit for n in numbers}) > 1:
        return False  # "ARR $12.4M" and "ARR up 40%" are different statements
    for a in numbers:
        for b in numbers:
            slack = max(a.absolute_precision, b.absolute_precision) / 2
            if abs(a.absolute - b.absolute) > slack + 1e-9:
                return True
    return False
