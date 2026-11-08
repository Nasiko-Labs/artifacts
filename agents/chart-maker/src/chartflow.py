"""Pure: a dataset and a spec -> every chart rendering, the summary, the notes and the envelope.
All arithmetic (totals, shares, ranks, scales) happens here and in the renderers, never in the model."""

from __future__ import annotations

from dataclasses import dataclass

import fmt
import mermaid
import rules
import svgchart
import textchart
from chartspec import Spec
from dataset import Dataset

MAX_TABLE_ROWS = 50
MAX_FAILED_LISTED = 5
LOW_PARSE_SHARE = 0.10
TYPE_NAMES = {"bar": "Bar chart", "column": "Column chart", "line": "Line chart", "stacked": "Stacked bar chart",
              "share": "Share chart"}


@dataclass(frozen=True)
class ChartOut:
    chart_type: str
    title: str
    summary: str
    text_chart: str
    table_md: str
    svg: str
    html: str
    mermaid: str | None
    notes: tuple[str, ...]
    confidence: str
    needs_human: bool
    items: tuple[dict, ...]


def build(ds: Dataset, spec: Spec, extra_notes: tuple[str, ...] = (), repaired: bool = False) -> ChartOut:
    choice = rules.choose(ds, spec.type_ask, spec.model_type)
    shown, fold_notes = rules.arrange(ds, choice.type, spec.sort, spec.top_n)
    svg, html = svgchart.render(shown, choice.type, spec.title)
    notes = (*choice.notes, *fold_notes, *_data_notes(ds), *extra_notes)
    if choice.type == "share":
        notes = ("Shares are shown as ranked percentage bars, which read more accurately than a pie.", *notes)
    low = ds.total_cells > 0 and ds.failed_cells / ds.total_cells > LOW_PARSE_SHARE
    medium = bool(fold_notes or choice.overridden or ds.merged_rows or spec.columns_asked or repaired)
    confidence = "low" if low else "medium" if medium else "high"
    return ChartOut(
        chart_type=choice.type, title=spec.title, summary=summary(shown, ds, choice.type, spec.title),
        text_chart=textchart.render(shown, choice.type), table_md=table(shown, choice.type), svg=svg, html=html,
        mermaid=mermaid.pie(shown, spec.title) if choice.mermaid_pie else None, notes=notes,
        confidence=confidence, needs_human=low or choice.overridden or ds.excluded_rows > 0,
        items=tuple({"series": s, "points": [{"label": lb, "value": v} for lb, v in zip(shown.labels, vals, strict=True)]}
                    for s, vals in zip(shown.series, shown.values, strict=True)))


def summary(shown: Dataset, full: Dataset, chart_type: str, title: str) -> str:
    head = f"{TYPE_NAMES[chart_type]} of {title}"
    count = f"{full.total_categories or len(shown.labels)} categories"
    decimals = fmt.decimals_for(v for s in shown.values for v in s)
    if chart_type == "line":
        parts = [f"{len(shown.labels)} points from {shown.labels[0]} to {shown.labels[-1]}"]
        for name, values in zip(shown.series, shown.values, strict=True):
            parts.append(f"{name} {fmt.number(values[0], decimals)} → {fmt.number(values[-1], decimals)}")
        return f"{head}: " + ", ".join(parts)
    if len(shown.series) > 1:
        return f"{head}: {count} × {len(shown.series)} series ({', '.join(shown.series)})"
    values = shown.values[0]
    total = sum(values)
    candidates = range(len(values) - 1) if shown.other_count and len(values) > 1 else range(len(values))
    top = max(candidates, key=lambda i: values[i])
    return (f"{head}: {count}, total {fmt.number(total, decimals)}, largest {shown.labels[top]} "
            f"({fmt.percent(values[top], total)})")


def table(ds: Dataset, chart_type: str) -> str:
    share = chart_type == "share"
    header = [ds.x, *ds.series, *(["Share"] if share else [])]
    decimals = fmt.decimals_for(v for s in ds.values for v in s)
    total = sum(ds.values[0]) if share else 0.0
    rows = []
    for i, label in enumerate(ds.labels[:MAX_TABLE_ROWS]):
        cells = [label, *(fmt.number(s[i], decimals) for s in ds.values)]
        rows.append(cells + ([fmt.percent(ds.values[0][i], total)] if share else []))
    lines = ["| " + " | ".join(_cell(c) for c in header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    if len(ds.labels) > MAX_TABLE_ROWS:
        lines.append(f"\n_The table shows the first {MAX_TABLE_ROWS} of {len(ds.labels):,} rows; say \"as csv\" "
                     "for all of them._")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return fmt.md(text)


def _data_notes(ds: Dataset) -> tuple[str, ...]:
    notes = []
    if ds.failed_cells:
        listed = "; ".join(ds.failed[:MAX_FAILED_LISTED]) + ("; ..." if len(ds.failed) > MAX_FAILED_LISTED else "")
        notes.append(f"{ds.failed_cells} of {ds.total_cells} cells couldn't be read as numbers and count as 0 "
                     f"({listed}).")
    if ds.excluded_rows:
        notes.append(f"{ds.excluded_rows} row(s) had no readable number and are not on the chart.")
    if ds.merged_rows:
        notes.append(f"{ds.merged_rows} row(s) repeated a label and were added to it.")
    return tuple(notes)


def as_csv(ds: Dataset) -> str:
    decimals = fmt.decimals_for(v for s in ds.values for v in s)
    lines = [",".join(_csv(c) for c in (ds.x, *ds.series))]
    lines += [",".join([_csv(label), *(f"{s[i]:.{decimals}f}" for s in ds.values)]) for i, label in enumerate(ds.labels)]
    return "\n".join(lines)


def _csv(text: str) -> str:
    return f'"{text.replace(chr(34), chr(34) * 2)}"' if any(c in text for c in ',"\n') else text
