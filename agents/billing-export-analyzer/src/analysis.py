"""Pure: every statistic of one export, in code, as a JSON-able result (no LLM). The narrative,
causes and model recommendations are added later by narrate.py; confidence by envelope.py."""

from __future__ import annotations

from dataclasses import dataclass

import anomalies as an
import chart_text
import digest as dg
import evidence
import stats
import waste
from frame import Frame
from windows import compute


@dataclass(frozen=True)
class Options:
    window_days: int | None = None
    top: int = stats.TOP_MOVERS
    instruction: str = ""
    budget: tuple[float, int | None] | None = None

    def to_dict(self) -> dict:
        return {"window_days": self.window_days, "top": self.top, "instruction": self.instruction,
                "budget": list(self.budget) if self.budget else None}

    @staticmethod
    def from_dict(data: dict | None) -> Options:
        data = data or {}
        b = data.get("budget")
        return Options(window_days=int(data["window_days"]) if data.get("window_days") else None,
                       top=int(data.get("top") or stats.TOP_MOVERS), instruction=str(data.get("instruction") or ""),
                       budget=(float(b[0]), int(b[1]) if b[1] else None) if b else None)


def run(frame: Frame, options: Options, guessed: tuple[str, ...] = ()) -> dict:
    df = frame.df
    w = compute(df["date"], options.window_days)
    totals = stats.totals(frame, w)
    service_movers = stats.movers(df, w, "service", options.top)
    drivers = evidence.drivers(df, w, service_movers, frame.dims())
    top = [_mover(m, d) for m, d in zip(service_movers, drivers, strict=True)]
    accounts = [_mover(m, []) for m in stats.movers(df, w, "account", options.top)]
    episodes = [] if w.monthly else [a.to_dict() for a in an.find(df)]
    untagged = stats.untagged(frame, options.instruction)
    totals["untagged_share_pct"] = untagged["share_pct"] if untagged else None
    if options.budget:
        totals["budget"] = stats.budget(df, w, *options.budget)
    patterns, not_checked = waste.check(df, w)
    data = chart_text.chart_data(df, w.monthly, w.days)
    unit = "month" if (w.monthly or w.days >= chart_text.WEEKLY_UNDER_DAYS) else "week"
    not_computed = [*([w.note] if w.note else []), *([an.MONTHLY_NOTE] if w.monthly else []),
                    *([stats.NO_TAG_NOTE] if untagged is None else []), *not_checked]
    if options.budget and not totals.get("budget"):
        not_computed.append("Budget projection: the export doesn't cover that month with daily data.")
    result = {
        "period": {"start": w.start.strftime("%Y-%m-%d"), "end": w.end.strftime("%Y-%m-%d"), "days": w.days,
                   "granularity": "monthly" if w.monthly else "daily"},
        "window": {"label": w.label, "note": w.note, "comparable": w.comparable},
        "totals": totals, "top_movers": top, "account_movers": accounts, "anomalies": episodes,
        "untagged": untagged, "waste_patterns": [p.to_dict() for p in patterns], "not_computed": not_computed,
        "service_totals": dict(list(stats.service_totals(df).items())[:10]), "chart_data": data,
        "chart": chart_text.render(data, top, totals["currency"], unit),
        "rows_read": frame.rows_read, "rows_dropped": frame.rows_dropped, "empty_costs": frame.empty_costs,
        "notes": list(frame.notes), "cut": frame.cut, "guessed": list(guessed), "options": options.to_dict(),
        "narrative": "", "narrative_source": "template", "recommendations": _waste_recs(patterns), "dropped": 0,
    }
    result["digest"] = dg.build(result)
    return result


def _mover(m: stats.Mover, drivers: list[evidence.Driver]) -> dict:
    return {"name": m.name, "dimension": m.dimension, "prev": m.prev, "cur": m.cur, "change_abs": m.change_abs,
            "change_pct": m.change_pct, "status": m.status, "likely_cause": "",
            "evidence": [d.to_dict() for d in drivers]}


def _waste_recs(patterns: list[waste.Pattern]) -> list[dict]:
    return [{"id": p.id.lower(), "text": p.text, "est_monthly": p.est_monthly, "assumption": p.assumption,
             "evidence_ids": [p.id]} for p in patterns]
