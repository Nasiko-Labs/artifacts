"""Pure: facts for a ranking of categories (shape b) and a two-period comparison (shape c)."""

from __future__ import annotations

from collections.abc import Sequence

from display import amount, change, percent, plain, times
from fact import Draft
from table import Column

TIMES_AT = 1.5          # the gap is also written as "x times" from this ratio
MOVER_MIN_SHARE = 0.05  # the biggest % mover must hold this share of the total
MAX_MOVERS = 3


def _pairs(labels: Sequence[str], column: Column) -> list[tuple[str, float]]:
    return [(label, v) for label, v in zip(labels, column.values) if v is not None]


def shareable(column: Column, values: Sequence[float]) -> bool:
    return not column.unit.pct and all(v >= 0 for v in values) and sum(values) > 0


def ranking_facts(labels: Sequence[str], columns: Sequence[Column]) -> list[Draft]:
    main = columns[0]
    pairs = sorted(_pairs(labels, main), key=lambda p: -p[1])
    if len(pairs) < 2:
        return []
    unit, name = main.unit, main.name
    values = [v for _, v in pairs]
    (l1, v1), (l2, _) = pairs[0], pairs[1]
    out: list[Draft] = []
    a1 = amount(v1, unit)
    if shareable(main, values):
        total = sum(values)
        share, vt = percent(v1 / total * 100), amount(total, unit)
        out.append(Draft("ranking", f"{l1} has the highest {name}: {a1}, {share} of the total of {vt}.",
                         (a1, share, vt), {"leader": l1, "value": v1, "share_pct": v1 / total * 100}))
    else:
        out.append(Draft("ranking", f"{l1} has the highest {name}: {a1}.", (a1,), {"leader": l1, "value": v1}))
    if len(pairs) >= 4 and v1 > pairs[1][1] + pairs[2][1] and shareable(main, values):
        out.append(Draft("share", f"{l1} alone has more {name} than {l2} and {pairs[2][0]} combined.", (),
                         {"leader": l1}))
    out.append(_gap(pairs, main))
    if len(pairs) >= 5 and shareable(main, values):
        top = sum(v for _, v in pairs[:3]) / sum(values) * 100
        names = ", ".join(lbl for lbl, _ in pairs[:3])
        out.append(Draft("share", f"The top three ({names}) together have {percent(top)} of the total.",
                         (percent(top),), {"top3_share_pct": top}))
    low_label, low = pairs[-1]
    out.append(Draft("ranking", f"{low_label} has the lowest {name}: {amount(low, unit)}.", (amount(low, unit),),
                     {"last": low_label, "value": low}))
    out += [d for d in (_ties(pairs, main), *(_leader(labels, c, l1) for c in columns[1:])) if d is not None]
    return out


def _gap(pairs: list[tuple[str, float]], column: Column) -> Draft:
    (l1, v1), (l2, v2) = pairs[0], pairs[1]
    unit = column.unit
    a1, a2, gap = amount(v1, unit), amount(v2, unit), change(v1 - v2, unit)
    text = f"{l1}'s {a1} is {gap} more than {l2}'s {a2}"
    display = [a1, a2, gap]
    if v2 > 0 and v1 / v2 >= TIMES_AT and not unit.pct:
        display.append(times(v1 / v2))
        text += f", {display[-1]} as much"
    return Draft("gap", text + ".", tuple(display), {"gap": v1 - v2, "ratio": v1 / v2 if v2 else None})


def _ties(pairs: list[tuple[str, float]], column: Column) -> Draft | None:
    for value in dict.fromkeys(v for _, v in pairs):
        tied = [lbl for lbl, v in pairs if v == value]
        if len(tied) > 1:
            a = amount(value, column.unit)
            names = ", ".join(tied[:-1]) + f" and {tied[-1]}"
            return Draft("ranking", f"{names} are tied at {a}.", (a,), {"tied": tied, "value": value})
    return None


def _leader(labels: Sequence[str], column: Column, main_leader: str) -> Draft | None:
    pairs = _pairs(labels, column)
    if not pairs:
        return None
    label, value = max(pairs, key=lambda p: p[1])
    if label == main_leader:
        return None
    a = amount(value, column.unit)
    return Draft("ranking", f"On {column.name}, {label} is highest ({a}).", (a,), {"leader": label, "value": value})


def comparison_facts(labels: Sequence[str], before: Column, after: Column) -> list[Draft]:
    rows = [(lbl, a, b) for lbl, a, b in zip(labels, before.values, after.values) if a is not None and b is not None]
    if len(rows) < 2:
        return []
    unit, out = after.unit, []
    if not unit.pct:
        ta, tb = sum(a for _, a, _ in rows), sum(b for _, _, b in rows)
        out.append(_move("The total", ta, tb, before.name, after.name, unit, "change"))
    movers = sorted(rows, key=lambda r: -abs(r[2] - r[1]))[:MAX_MOVERS]
    out += [_move(lbl, a, b, before.name, after.name, unit, "mover") for lbl, a, b in movers if b != a]
    total = sum(abs(a) for _, a, _ in rows) or 1.0
    big = [r for r in rows if r[1] != 0 and abs(r[1]) / total >= MOVER_MIN_SHARE]
    if big and not unit.pct:
        lbl, a, b = max(big, key=lambda r: abs((r[2] - r[1]) / r[1]))
        pct = (b - a) / abs(a) * 100
        word, vp = "up" if pct > 0 else "down", percent(pct)
        out.append(Draft("mover", f"In percentage terms, {lbl} moved the most: {word} {vp}, from {amount(a, unit)} "
                                  f"to {amount(b, unit)}.", (vp, amount(a, unit), amount(b, unit)),
                         {"label": lbl, "change_pct": pct}))
    up, down = sum(b > a for _, a, b in rows), sum(b < a for _, a, b in rows)
    out.append(Draft("mover", f"{up} of {len(rows)} went up and {down} went down.",
                     (plain(up), plain(len(rows)), plain(down)), {"up": up, "down": down, "items": len(rows)}))
    return out


def _move(label: str, a: float, b: float, before: str, after: str, unit, kind: str) -> Draft:
    va, vb, vd = amount(a, unit), amount(b, unit), change(b - a, unit)
    pct = None if a == 0 or unit.pct else (b - a) / abs(a) * 100
    word = "up" if b > a else "down" if b < a else "level"
    size = f"{word} {vd}" + (f" ({percent(pct)})" if pct is not None else "") if word != "level" else "no change"
    text = f"{label} went from {va} in {before} to {vb} in {after}, {size}."
    display = (va, vb, vd) + ((percent(pct),) if pct is not None else ())
    return Draft(kind, text, display, {"label": label, "before": a, "after": b, "change_pct": pct})
