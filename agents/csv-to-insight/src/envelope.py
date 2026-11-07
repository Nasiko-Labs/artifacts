"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from code checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from checks import Checked
from fmt import fmt
from profiling import Table
from result import Result

LOW_ROWS = 30
LOW_UNPARSED_SHARE = 0.2


@dataclass(frozen=True)
class Signals:
    used_default: bool = False
    dropped: int = 0
    png_failed: bool = False


def confidence(table: Table, checked: Checked, skipped: Sequence[dict], signals: Signals) -> str:
    p = table.profile
    if p.rows < LOW_ROWS or p.unparsed_share > LOW_UNPARSED_SHARE or not p.of("numeric", "datetime"):
        return "low"
    if signals.used_default or checked.fell_back or skipped or signals.dropped or checked.dropped:
        return "medium"
    return "high"


def build(table: Table, results: Sequence[Result], skipped: Sequence[dict], checked: Checked,
          data_caveats: Sequence[str], chart: Result | None, chart_text: str, signals: Signals,
          png: bool = False, notes: Sequence[str] = ()) -> dict:
    p = table.profile
    level = confidence(table, checked, skipped, signals)
    findings = [f.to_dict() for f in checked.findings]
    caveats = [{"text": c, "fact_ids": []} for c in data_caveats] + [c.to_dict() for c in checked.caveats]
    chart_title = chart.chart["title"] if chart and chart.chart else ""
    summary = (f"{len(findings)} findings from {fmt(p.rows)} rows × {fmt(p.cols)} columns"
               + (f"; chart: {chart_title}." if chart_title else "."))
    return {
        "summary": summary,
        "items": findings,
        "findings": findings,
        "chart_file": "chart.png" if png else None,
        "caveats": caveats,
        "chart": chart_text,
        "chart_data": dict(chart.chart) if chart and chart.chart else None,
        "chart_result": chart.id if chart else None,
        "profile": {"rows": p.rows, "cols": p.cols, "dtypes": {c.name: c.dtype for c in p.columns},
                    "counts": p.counts()},
        "analyses_run": [{"id": r.id, "kind": r.kind, "columns": dict(r.columns)} for r in results],
        "analyses_skipped": [dict(s) for s in skipped],
        "dropped": signals.dropped + checked.dropped,
        "fell_back": checked.fell_back,
        "default_plan": signals.used_default,
        "notes": list(notes),
        "confidence": level,
        "needs_human": level == "low",
    }
