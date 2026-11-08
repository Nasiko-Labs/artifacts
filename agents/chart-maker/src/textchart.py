"""Pure: the chart people actually see in chat, as monospace text inside a ```text block.

Bars are BAR_CELLS wide with eighth-block precision; labels are cut to 24 characters (the full label
is in the table); values are right-aligned with thousands separators."""

from __future__ import annotations

import fmt
from dataset import Dataset

BAR_CELLS = 40
FRACTIONS = "▏▎▍▌▋▊▉"
FULL = "█"
STACK_FILLS = "█▓▒░"
SPARKS = "▁▂▃▄▅▆▇█"
ZERO_MARK = "│"


def bar(value: float, scale: float, cells: int = BAR_CELLS) -> str:
    """|value| / scale of `cells` cells, in eighths."""
    if scale <= 0 or value == 0:
        return ""
    eighths = round(abs(value) / scale * cells * 8)
    full, rest = divmod(eighths, 8)
    return FULL * full + (FRACTIONS[rest - 1] if rest else "")


def render(ds: Dataset, chart_type: str) -> str:
    if chart_type == "stacked":
        return _stacked(ds)
    if chart_type == "line":
        return _line(ds)
    if len(ds.series) > 1:
        return _grouped(ds)
    return _single(ds, share=chart_type == "share")


def _labels(ds: Dataset) -> tuple[list[str], int]:
    labels = [fmt.short_label(lb) for lb in ds.labels]
    return labels, max((len(lb) for lb in labels), default=0)


def _single(ds: Dataset, share: bool) -> str:
    values = ds.values[0]
    labels, width = _labels(ds)
    decimals = fmt.decimals_for(values)
    shown = [fmt.number(v, decimals) for v in values]
    vwidth = max(len(s) for s in shown)
    total = sum(values)
    lines = []
    for label, value, text in zip(labels, values, shown, strict=True):
        tail = f" {fmt.percent(value, total):>4}" if share else ""
        lines.append(f"{label:<{width}}  {_signed_bar(value, values)} {text:>{vwidth}}{tail}".rstrip())
    return "\n".join(lines)


def _signed_bar(value: float, values: tuple[float, ...]) -> str:
    """Positive-only data: a plain bar padded to BAR_CELLS. With negatives: a zero marker splits the
    cells in proportion to the largest negative and positive values."""
    top = max((v for v in values if v > 0), default=0.0)
    low = -min((v for v in values if v < 0), default=0.0)
    if low == 0:
        return f"{bar(value, top):<{BAR_CELLS}}"
    left = max(1, round(BAR_CELLS * low / (low + top)))
    right = BAR_CELLS - left
    neg = bar(value, low, left) if value < 0 else ""
    pos = bar(value, top, right) if value > 0 else ""
    return f"{neg:>{left}}{ZERO_MARK}{pos:<{right}}"


def _grouped(ds: Dataset) -> str:
    labels, width = _labels(ds)
    names = [fmt.short_label(s) for s in ds.series]
    nwidth = max(len(n) for n in names)
    every = [v for s in ds.values for v in s]
    decimals = fmt.decimals_for(every)
    vwidth = max(len(fmt.number(v, decimals)) for v in every)
    lines = []
    for i, label in enumerate(labels):
        for k, name in enumerate(names):
            value = ds.values[k][i]
            head = label if k == 0 else ""
            lines.append(f"{head:<{width}}  {name:<{nwidth}}  {_signed_bar(value, tuple(every))} "
                         f"{fmt.number(value, decimals):>{vwidth}}".rstrip())
    return "\n".join(lines)


def _stacked(ds: Dataset) -> str:
    labels, width = _labels(ds)
    totals = [sum(s[i] for s in ds.values) for i in range(len(ds.labels))]
    scale = max(totals, default=0.0)
    decimals = fmt.decimals_for(totals)
    vwidth = max(len(fmt.number(t, decimals)) for t in totals)
    lines = []
    for i, label in enumerate(labels):
        segments = "".join(STACK_FILLS[k] * round(ds.values[k][i] / scale * BAR_CELLS) if scale else ""
                           for k in range(len(ds.series)))
        lines.append(f"{label:<{width}}  {segments:<{BAR_CELLS}} {fmt.number(totals[i], decimals):>{vwidth}}")
    legend = "   ".join(f"{STACK_FILLS[k]} {name}" for k, name in enumerate(ds.series))
    return "\n".join([*lines, "", legend])


def sparkline(values: tuple[float, ...]) -> str:
    low, high = min(values), max(values)
    if high == low:
        return SPARKS[3] * len(values)
    return "".join(SPARKS[round((v - low) / (high - low) * (len(SPARKS) - 1))] for v in values)


def _line(ds: Dataset) -> str:
    names = [fmt.short_label(s) for s in ds.series]
    nwidth = max(len(n) for n in names)
    decimals = fmt.decimals_for(v for s in ds.values for v in s)
    lines = [f"{ds.labels[0]} → {ds.labels[-1]}"]
    for name, values in zip(names, ds.values, strict=True):
        lines.append(f"{name:<{nwidth}}  {sparkline(values)}  {fmt.number(values[0], decimals)} → "
                     f"{fmt.number(values[-1], decimals)} (min {fmt.number(min(values), decimals)}, "
                     f"max {fmt.number(max(values), decimals)})")
    if len(ds.series) == 1:
        lines += ["", _single(ds, share=False)]
    return "\n".join(lines)
