"""Pure: the chart as text (the chat renders markdown only). A line chart is a sparkline with the first,
last and peak values labelled plus a table of the last 12 buckets; a bar chart is a fenced block of
proportional bars with the exact value at the end of each line."""

from __future__ import annotations

from collections.abc import Sequence

from checks import cell
from fmt import fmt
from result import Result

SPARKS = "▁▂▃▄▅▆▇█"
BAR_WIDTH = 30
LINE_TABLE_ROWS = 12
LABEL_WIDTH = 18


def sparkline(values: Sequence[float | None]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return ""
    low, high = min(present), max(present)
    span = high - low
    out = []
    for v in values:
        if v is None:
            out.append(" ")
        elif span == 0:
            out.append(SPARKS[len(SPARKS) // 2])
        else:
            out.append(SPARKS[round((v - low) / span * (len(SPARKS) - 1))])
    return "".join(out)


def bars(labels: Sequence[str], values: Sequence[float]) -> str:
    top = max((abs(v) for v in values), default=0) or 1
    width = min(LABEL_WIDTH, max((len(lab) for lab in labels), default=1))
    lines = []
    for lab, v in zip(labels, values):
        n = round(abs(v) / top * BAR_WIDTH)
        lab = lab.replace("`", "'")
        short = (lab[: width - 1] + "…") if len(lab) > width else lab
        lines.append(f"{short.ljust(width)} {'█' * max(n, 1 if v else 0)} {fmt(v)}")
    return "```\n" + "\n".join(lines) + "\n```"


def render_chart(result: Result | None) -> str:
    if result is None or not result.chart:
        return ""
    chart = result.chart
    name, values = next(iter(chart["series"].items()))
    title = f"**Chart: {cell(chart['title'])}**"
    if chart["kind"] == "line":
        return "\n".join([title, _line(chart["x"], values), "", _table(chart["x"], values, name)])
    return "\n".join([title, bars([str(x) for x in chart["x"]], [float(v) for v in values])])


def _line(labels: Sequence[str], values: Sequence[float | None]) -> str:
    present = [(lab, v) for lab, v in zip(labels, values) if v is not None]
    if not present:
        return "No values to plot."
    first, last = present[0], present[-1]
    peak = max(present, key=lambda p: p[1])
    return (f"`{sparkline(values)}`  {fmt(first[1])} ({first[0]}) → {fmt(last[1])} ({last[0]}); "
            f"peak {fmt(peak[1])} ({peak[0]})")


def _table(labels: Sequence[str], values: Sequence[float | None], name: str) -> str:
    shown = list(zip(labels, values))[-LINE_TABLE_ROWS:]
    lines = [f"| {cell('bucket')} | {cell(name)} |", "|---|---:|"]
    lines += [f"| {cell(lab)} | {fmt(v) if v is not None else '-'} |" for lab, v in shown]
    return "\n".join(lines)
