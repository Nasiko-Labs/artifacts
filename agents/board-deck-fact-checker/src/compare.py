"""Pure: compare one deck claim with its source, in code. Every rule is a named constant and is listed in
the report's "Rules used" footer (`RULES`). The model never says whether two numbers match.

Tolerance, applied in order (the first rule that holds decides `ok`):
1. exact equality -> `ok`; rounding: |deck - source| <= half the last written digit -> `ok_rounded`;
2. truncation (`ROUND_DOWN_OK`): the deck dropped digits instead of rounding -> `ok_rounded`, labelled;
3. strict mode disables 1 (rounding) and 2: exact match to the written digits only;
4. loose mode adds: relative difference <= 1 %.
Growth, ratio, sum and difference claims are recomputed from their operand cells first. A different
period is a `period_mismatch` even when the numbers are equal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import figures
import periods
from records import Cell, Claim, Comparison, Match

ROUND_DOWN_OK = True
LOOSE_RELATIVE = 0.01
MATERIAL = 0.05
MINOR = 0.01
MAGNITUDE_GAP = 10.0
FRACTION_LIMIT = 1.5  # an unformatted cell at or under this, against a percent claim, is a fraction
_EPS = 1e-9

MODES = ("default", "strict", "loose")
RULES = {
    "rounding": "within half of the last digit the deck shows (e.g. $4.2M covers 4.15M to 4.25M)",
    "truncation": "a deck figure that drops digits instead of rounding is accepted and labelled",
    "strict": "on request: exact match to the digits the deck shows, no rounding slack",
    "loose": "on request: also accepted within 1 % relative",
    "period": "the sheet's period must equal the claim's period, even when the numbers are equal",
    "recompute": "growth, ratios, sums and differences are recomputed from the source cells",
    "materiality": "material >= 5 %, minor 1-5 %, rounding-sized under 1 % (relative to the source)",
}


def compare(claim: Claim, match: Match, cells: Mapping[str, Cell], mode: str = "default") -> Comparison:
    number = claim.number
    if match.note or not (match.cell_ids or match.op):
        return _unverifiable(claim, match.note or "no matching cell in the source")
    if match.op:
        return _formula(claim, number, match, cells, mode)
    chosen = [cells[c] for c in match.cell_ids if c in cells]
    if not chosen:
        return _unverifiable(claim, "no matching cell in the source")
    values = [_normalised(c, number) for c in chosen]
    if len(chosen) > 1 and not _equal(values[0][0], values[1][0]):
        return _ambiguous(claim, number, chosen, values)
    cell, (source, note) = chosen[0], values[0]
    unit_problem = _unit_problem(claim, number, cell, source)
    if unit_problem:
        return _result(claim, number, "unit_mismatch", source, [cell], unit_problem)
    status, rule = _verdict(number, source, mode)
    return _with_period(claim, _result(claim, number, status, source, [cell], _join(note, rule, cell.scale_note)),
                        cell)


# --- values ---------------------------------------------------------------------------------------------

def _normalised(cell: Cell, number: figures.Number) -> tuple[float, str]:
    """The cell's value in the claim's units (absolute), plus a note when it was read as a fraction."""
    if number.unit != "percent":
        return cell.source_value, ""
    if cell.percent:
        return cell.value, ""
    if abs(cell.source_value) <= FRACTION_LIMIT:
        return cell.source_value * 100, "fraction read as a percentage"
    return cell.source_value, ""


def _unit_problem(claim: Claim, number: figures.Number, cell: Cell, source: float) -> str:
    if cell.percent and number.unit not in ("percent", "ratio"):
        return "the source cell is a percentage, the claim is not"
    deck = number.absolute
    if cell.scale_known or not deck or not source:
        return ""
    gap = max(abs(deck), abs(source)) / min(abs(deck), abs(source))
    if gap >= MAGNITUDE_GAP:
        return f"the sheet states no scale and the values differ about {gap:,.0f}x (units or thousands?)"
    return ""


def _verdict(number: figures.Number, source: float, mode: str) -> tuple[str, str]:
    deck, written = number.value, source / number.scale
    diff, precision = abs(deck - written), number.precision
    if diff <= _EPS * max(1.0, abs(written)):
        return "ok", "exact"
    if mode == "strict":
        return "mismatch", "strict: exact match to the written digits required"
    if diff <= precision / 2 + _EPS:
        return "ok_rounded", f"within rounding (±{figures.fmt(precision / 2 * number.scale, number, 2)})"
    same_sign = (deck >= 0) == (written >= 0)
    if ROUND_DOWN_OK and same_sign and 0 <= abs(written) - abs(deck) < precision - _EPS:
        return "ok_rounded", "truncated, not rounded"
    if mode == "loose" and written and diff / abs(written) <= LOOSE_RELATIVE + _EPS:
        return "ok_rounded", "within 1 % (loose)"
    return "mismatch", f"not within rounding (±{figures.fmt(precision / 2 * number.scale, number, 2)})"


def _equal(a: float, b: float) -> bool:
    return abs(a - b) <= _EPS * max(1.0, abs(a), abs(b))


# --- formulas -------------------------------------------------------------------------------------------

def _formula(claim: Claim, number: figures.Number, match: Match, cells: Mapping[str, Cell],
             mode: str) -> Comparison:
    operands = [cells[c] for c in match.operands if c in cells]
    if len(operands) < 2 or len(operands) != len(match.operands):
        return _unverifiable(claim, "the proposed calculation has missing operands")
    if match.op in ("growth", "difference") and claim.kind in ("growth", "percent") and number.unit == "percent":
        return _change(claim, number, match.op, operands, mode)
    values = [c.source_value for c in operands]
    if match.op == "sum":
        source = sum(values)
    elif match.op == "difference":
        source = values[1] - values[0]
    else:  # ratio
        if values[1] == 0:
            return _unverifiable(claim, "unverifiable (zero denominator)")
        source = values[0] / values[1]
    status, rule = _verdict(number, source, mode)
    note = _join(f"recomputed: {match.op} of {_refs(operands)}", rule)
    return _with_period(claim, _result(claim, number, status, source, operands, note), operands[-1])


def _change(claim: Claim, number: figures.Number, op: str, operands: Sequence[Cell], mode: str) -> Comparison:
    """A growth or percent-change claim: relative change ("up 12 %") or points ("up 3 pts"); with the basis
    unknown both are computed and either may match (`basis assumed`)."""
    pair = operands[:2]
    percent_like = all(c.percent or abs(c.source_value) <= FRACTION_LIMIT for c in pair)
    old, new = (_percent_value(c) if percent_like else c.source_value for c in pair)
    relative = None if old == 0 else (new - old) / abs(old) * 100
    basis = "points" if number.points else claim.basis
    if not percent_like:  # points of a currency or a count mean nothing: relative change only
        basis = "relative"
    options = [("relative", relative), ("points", new - old)]
    if basis in ("relative", "points"):
        options = [o for o in options if o[0] == basis]
    elif op == "difference":
        options = options[::-1]
    usable = [(b, v) for b, v in options if v is not None]
    if not usable:
        return _unverifiable(claim, "unverifiable (zero denominator)")
    picked = next(((b, v, _verdict(number, v, mode)) for b, v in usable
                   if _verdict(number, v, mode)[0] != "mismatch"), None)
    b, value, (status, rule) = picked or (*usable[0], _verdict(number, usable[0][1], mode))
    assumed = "" if basis in ("relative", "points") else f"basis assumed ({b})"
    note = _join(f"recomputed: {b} change from {_refs(operands[:2])}", assumed, rule)
    return _with_period(claim, _result(claim, number, status, value, list(operands[:2]), note), operands[1])


def _percent_value(cell: Cell) -> float:
    return cell.value if cell.percent else cell.source_value * 100


# --- results --------------------------------------------------------------------------------------------

def _with_period(claim: Claim, result: Comparison, cell: Cell) -> Comparison:
    wanted = periods.parse(claim.period) if claim.period else None
    if wanted is None or result.status in ("unit_mismatch", "ambiguous", "unverifiable"):
        return result
    found = next((p for p in (periods.parse(t) for t in (cell.col_label, cell.row_label, cell.tab)) if p), None)
    if found is None:
        return _replace(result, warnings=(*result.warnings, "period_unverified: the cell has no period label"))
    if periods.same(wanted, found):
        return result
    values = "values are equal" if result.status.startswith("ok") else "values differ too"
    note = f"the sheet column is {found.label()}, the claim says {wanted.label()} ({values})"
    return _replace(result, status="period_mismatch", rule_note=note,
                    materiality=result.materiality or "material")


def _result(claim: Claim, number: figures.Number, status: str, source: float, cells: Sequence[Cell],
            note: str) -> Comparison:
    deck = number.absolute
    relative = abs(deck - source) / abs(source) if source else None
    mismatch = status in ("mismatch", "unit_mismatch")
    return Comparison(claim=claim, status=status, deck_value=claim.value_text,
                      source_value=figures.fmt(source, number), source_ref=_refs(cells),
                      source_url=next((c.url for c in cells if c.url), ""),
                      delta=_signed(deck - source, number) if mismatch else "",
                      relative_delta=round(relative, 4) if relative is not None and mismatch else None,
                      materiality=materiality(relative) if status == "mismatch" else "", rule_note=note)


def _ambiguous(claim: Claim, number: figures.Number, cells: Sequence[Cell],
               values: Sequence[tuple[float, str]]) -> Comparison:
    shown = " or ".join(figures.fmt(v, number) for v, _ in values)
    return Comparison(claim=claim, status="ambiguous", deck_value=claim.value_text, source_value=shown,
                      source_ref=_refs(cells), source_url=next((c.url for c in cells if c.url), ""),
                      rule_note="two plausible source cells with different values; both listed")


def _unverifiable(claim: Claim, note: str) -> Comparison:
    return Comparison(claim=claim, status="unverifiable", deck_value=claim.value_text, rule_note=note)


def materiality(relative: float | None) -> str:
    if relative is None or relative >= MATERIAL:
        return "material"
    return "minor" if relative >= MINOR else "rounding-sized"


def _signed(delta: float, number: figures.Number) -> str:
    """A percent claim's difference is in percentage points."""
    like = replace(number, points=True) if number.unit == "percent" else number
    text = figures.fmt(delta, like)
    return text if text.startswith("-") else f"+{text}"


def _refs(cells: Sequence[Cell]) -> str:
    return ", ".join(c.address for c in cells)


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _replace(result: Comparison, **changes) -> Comparison:
    return replace(result, **changes)
