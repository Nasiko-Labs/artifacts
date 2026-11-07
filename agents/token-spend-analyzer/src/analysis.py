"""Pure: one analysis of a normalised frame — aggregates, waste findings and the digest — as a plain,
storable dict. The recommendation step (recommend.py) fills `summary` and `recommendations` afterwards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aggregate
import digest
import prices
import waste
from frame import Frame

DIMENSION_FIELDS = (("model", "by_model"), ("team", "by_team"), ("task", "by_task"))


@dataclass(frozen=True)
class Options:
    top: int = aggregate.TOP_N
    instruction: str = ""

    def to_dict(self) -> dict:
        return {"top": self.top, "instruction": self.instruction}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Options:
        data = data or {}
        return Options(int(data.get("top") or aggregate.TOP_N), str(data.get("instruction") or ""))


def team_label(frame: Frame) -> str:
    return "agent" if frame.shape == "nasiko" else "team"


def run(frame: Frame, options: Options) -> dict:
    df = frame.df
    tables = {name: (aggregate.group(df, dim, options.top) if dim in frame.dims else [])
              for dim, name in DIMENSION_FIELDS}
    not_computed = [f"{name}: not in this export" for dim, name in DIMENSION_FIELDS if dim not in frame.dims]
    findings, not_checked = waste.find(frame)
    totals = aggregate.totals(frame)
    mix = totals["cost_basis_mix"]
    meta = frame.meta
    result: dict[str, Any] = {
        "shape": frame.shape, "team_label": team_label(frame), "period": aggregate.period(df), "totals": totals,
        **tables, "cross": aggregate.cross(df) if {"team", "model"} <= set(frame.dims) else [],
        "change": aggregate.change(frame), "waste": findings, "not_computed": not_computed,
        "not_checked": not_checked, "rows_read": frame.rows_read, "rows_used": len(df),
        "rows_dropped": frame.rows_dropped, "dropped": dict(frame.dropped), "capped": frame.capped,
        "dims": list(frame.dims), "has_status": frame.has_status,
        "unpriced_rows": int(mix.get("unknown", 0)), "computed_rows": int(mix.get("computed", 0)),
        "price_as_of": prices.AS_OF if mix.get("computed") else None,
        "meta": {"unpriced_calls": int(meta.get("unpriced_calls") or 0),
                 "estimated_cost": float(meta.get("estimated_cost") or 0.0),
                 "summary_total_cost": float(meta.get("summary_total_cost") or 0.0),
                 "previous": dict(meta.get("previous") or {}), "capped_agents": list(meta.get("capped_agents") or [])},
        "options": options.to_dict(), "summary": "", "recommendations": [], "rec_source": "none", "recs_dropped": 0,
    }
    if frame.capped:
        result["not_checked"].append("Only the first 200,000 rows were read; later rows are not included")
    if meta.get("capped_agents"):
        result["not_checked"].append("The export marks these agents' token counts as capped (under-counted): "
                                     + ", ".join(meta["capped_agents"]))
    result["digest"] = digest.build(result)
    return result
