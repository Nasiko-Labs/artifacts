"""Pure: Nasiko's own FinOps dashboard JSON (GET /api/observability/finops/dashboard). Each `data.agents[]` row
becomes one frame row with the agent in the team slot (reported "by agent"); there is no model dimension.
`summary.unpriced_calls` / `estimated_cost` and each agent's `previous` window travel as metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd


def data_of(obj: Any) -> Mapping | None:
    if not isinstance(obj, Mapping):
        return None
    inner = obj.get("data") if isinstance(obj.get("data"), Mapping) else obj
    if isinstance(inner.get("summary"), Mapping) and isinstance(inner.get("agents"), list):
        return inner
    return None


def is_nasiko(obj: Any) -> bool:
    return data_of(obj) is not None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read(obj: Any) -> tuple[pd.DataFrame, dict]:
    data = data_of(obj) or {}
    rows, previous, capped = [], {}, []
    for agent in data.get("agents") or []:
        if not isinstance(agent, Mapping):
            continue
        name = str(agent.get("agent_name") or agent.get("agent_id") or "unknown")
        rows.append({"team": name, "input": _num(agent.get("prompt_tokens")),
                     "output": _num(agent.get("completion_tokens")),
                     "cached_in": _num(agent.get("cache_read_tokens")),
                     "cache_write": _num(agent.get("cache_creation_tokens")),
                     "requests": _num(agent.get("operations")), "cost": _num(agent.get("total_cost"))})
        prev = agent.get("previous")
        if isinstance(prev, Mapping):
            previous[name] = {"cost": _num(prev.get("total_cost")), "requests": _num(prev.get("operations"))}
        if agent.get("is_capped"):
            capped.append(name)
    summary = data.get("summary") or {}
    meta = {"unpriced_calls": int(_num(summary.get("unpriced_calls"))),
            "estimated_cost": _num(summary.get("estimated_cost")),
            "summary_total_cost": _num(summary.get("total_cost")),
            "previous": previous, "capped_agents": capped}
    return pd.DataFrame(rows, columns=["team", "input", "output", "cached_in", "cache_write", "requests", "cost"]), meta
