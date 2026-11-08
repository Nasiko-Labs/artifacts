"""Pure: the downloadable picture. Horizontal bars (bar, share, grouped bars, stacked) or an x/y plot
(columns, line), drawn from the arranged dataset with code-computed scales and nice ticks."""

from __future__ import annotations

import fmt
import svgdoc
from dataset import Dataset
from svgdoc import WIDTH, label
from ticks import ticks

TOP = 48
BAR_H = 18
GROUP_GAP = 10
RIGHT = 90
PLOT_H = 300
X_LABEL_CHARS = 12
CHAR_PX = 7


def render(ds: Dataset, chart_type: str, title: str) -> tuple[str, str]:
    """(standalone svg, html page)."""
    desc = table_text(ds)
    if chart_type in ("column", "line"):
        height, body = _xy(ds, chart_type)
    else:
        height, body = _horizontal(ds, chart_type)
    markup = svgdoc.svg(height, title, f"{title}. {desc}" if len(desc) < 600 else title, desc, body)
    return markup, svgdoc.page(title, markup)


def table_text(ds: Dataset) -> str:
    decimals = fmt.decimals_for(v for s in ds.values for v in s)
    rows = []
    for i, name in enumerate(ds.labels):
        cells = ", ".join(f"{s} {fmt.number(ds.values[k][i], decimals)}" for k, s in enumerate(ds.series))
        rows.append(f"{name}: {cells}")
    return "; ".join(rows)


def _domain(ds: Dataset, chart_type: str) -> tuple[float, ...]:
    if chart_type == "stacked":
        totals = [sum(s[i] for s in ds.values) for i in range(len(ds.labels))]
        return ticks(0.0, max(totals, default=0.0))
    every = [v for s in ds.values for v in s]
    return ticks(min(every, default=0.0), max(every, default=0.0))


def _horizontal(ds: Dataset, chart_type: str) -> tuple[int, list[str]]:
    names = [fmt.short_label(lb) for lb in ds.labels]
    left = 16 + CHAR_PX * max((len(n) for n in names), default=4)
    width = WIDTH - left - RIGHT
    axis = _domain(ds, chart_type)
    lo, hi = axis[0], axis[-1]

    def x(v: float) -> float:
        return left + (v - lo) / (hi - lo) * width

    per_group = 1 if chart_type in ("stacked", "share") or len(ds.series) == 1 else len(ds.series)
    group_h = per_group * BAR_H + GROUP_GAP
    bottom = TOP + group_h * len(ds.labels)
    decimals = fmt.decimals_for(v for s in ds.values for v in s)
    body = _x_ticks(axis, x, bottom, decimals)
    total = sum(ds.values[0]) if chart_type == "share" else 0.0
    for i, name in enumerate(names):
        y0 = TOP + i * group_h
        body.append(label(left - 8, y0 + per_group * BAR_H / 2 + 4, name, "end"))
        if chart_type == "stacked":
            body += _stack(ds, i, x, y0)
            continue
        for k in range(per_group):
            value = ds.values[k][i]
            y = y0 + k * BAR_H
            start, end = sorted((x(0.0), x(value)))
            body.append(f'<rect class="s{k}" x="{start:.1f}" y="{y:.1f}" width="{end - start:.1f}" '
                        f'height="{BAR_H - 3}"/>')
            shown = fmt.number(value, decimals) + (f" ({fmt.percent(value, total)})" if total else "")
            body.append(label(end + 4, y + BAR_H - 6, shown, extra=' class="muted"'))
    body.append(f'<line class="axis" x1="{x(0.0):.1f}" y1="{TOP - 4}" x2="{x(0.0):.1f}" y2="{bottom}"/>')
    legend_y = bottom + 40
    body += _legend(ds, legend_y) if len(ds.series) > 1 else []
    return legend_y + (24 if len(ds.series) > 1 else 0), body


def _stack(ds: Dataset, i: int, x, y0: float) -> list[str]:
    parts, offset = [], 0.0
    for k in range(len(ds.series)):
        value = ds.values[k][i]
        parts.append(f'<rect class="s{k}" x="{x(offset):.1f}" y="{y0:.1f}" width="{x(offset + value) - x(offset):.1f}" '
                     f'height="{BAR_H - 3}"/>')
        offset += value
    decimals = fmt.decimals_for(s[i] for s in ds.values)
    parts.append(label(x(offset) + 4, y0 + BAR_H - 6, fmt.number(offset, decimals), extra=' class="muted"'))
    return parts


def _x_ticks(axis: tuple[float, ...], x, bottom: float, decimals: int) -> list[str]:
    out = []
    for t in axis:
        out.append(f'<line class="grid" x1="{x(t):.1f}" y1="{TOP - 4}" x2="{x(t):.1f}" y2="{bottom}"/>')
        out.append(label(x(t), bottom + 16, fmt.number(t, _tick_decimals(axis, decimals)), "middle"))
    return out


def _tick_decimals(axis: tuple[float, ...], decimals: int) -> int:
    return max(decimals, fmt.decimals_for(axis))


def _legend(ds: Dataset, y: float) -> list[str]:
    out, x = [], 16.0
    for k, name in enumerate(ds.series):
        short = fmt.short_label(name)
        out.append(f'<rect class="s{k}" x="{x:.1f}" y="{y - 10:.1f}" width="12" height="12"/>')
        out.append(label(x + 16, y, short))
        x += 32 + CHAR_PX * len(short)
    return out


def _xy(ds: Dataset, chart_type: str) -> tuple[int, list[str]]:
    axis = _domain(ds, chart_type)
    lo, hi = axis[0], axis[-1]
    left, right, bottom = 72, WIDTH - 24, TOP + PLOT_H
    band = (right - left) / max(1, len(ds.labels))

    def y(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * PLOT_H

    decimals = fmt.decimals_for(v for s in ds.values for v in s)
    body = []
    for t in axis:
        body.append(f'<line class="grid" x1="{left}" y1="{y(t):.1f}" x2="{right}" y2="{y(t):.1f}"/>')
        body.append(label(left - 6, y(t) + 4, fmt.number(t, _tick_decimals(axis, decimals)), "end"))
    every = max(1, round(len(ds.labels) / 12))
    for i, name in enumerate(ds.labels):
        if i % every == 0:
            body.append(label(left + band * (i + 0.5), bottom + 18, fmt.short_label(name, X_LABEL_CHARS), "middle"))
    body += _lines(ds, left, band, y) if chart_type == "line" else _columns(ds, left, band, y)
    body.append(f'<line class="axis" x1="{left}" y1="{y(0.0):.1f}" x2="{right}" y2="{y(0.0):.1f}"/>')
    legend_y = bottom + 48
    body += _legend(ds, legend_y) if len(ds.series) > 1 else []
    return legend_y + (24 if len(ds.series) > 1 else 0), body


def _columns(ds: Dataset, left: float, band: float, y) -> list[str]:
    out, n = [], len(ds.series)
    inner = band * 0.8 / n
    for i in range(len(ds.labels)):
        for k in range(n):
            top, base = sorted((y(ds.values[k][i]), y(0.0)))
            x0 = left + band * i + band * 0.1 + inner * k
            out.append(f'<rect class="s{k}" x="{x0:.1f}" y="{top:.1f}" width="{inner - 2:.1f}" height="{base - top:.1f}"/>')
    return out


def _lines(ds: Dataset, left: float, band: float, y) -> list[str]:
    out = []
    for k, values in enumerate(ds.values):
        points = " ".join(f"{left + band * (i + 0.5):.1f},{y(v):.1f}" for i, v in enumerate(values))
        out.append(f'<polyline class="s{k} line" points="{points}"/>')
        out += [f'<circle class="s{k}" cx="{left + band * (i + 0.5):.1f}" cy="{y(v):.1f}" r="3"/>'
                for i, v in enumerate(values)]
    return out
