"""Pure: exact, pre-formatted strings for the model (and the renderer). The model copies these and never
computes; checks.py accepts a number in model text only when it is one of these strings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}
WHOLE_FROM = 100.0


def symbol(currency: str | None) -> str:
    if not currency:
        return "$"
    return SYMBOLS.get(currency.upper(), f"{currency.upper()} ")


def money(value: float, currency: str | None = None, signed: bool = False) -> str:
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    amount = abs(value)
    body = f"{amount:,.0f}" if amount >= WHOLE_FROM else f"{amount:,.2f}"
    return f"{sign}{symbol(currency)}{body}"


def pct(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "new"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def sigma(value: float) -> str:
    return f"{value:+.1f}σ"


def mover_pct(m: Mapping[str, Any]) -> str:
    if m.get("status") == "new":
        return "new"
    if m.get("status") == "gone":
        return "gone"
    return pct(m.get("change_pct"))


def build(result: Mapping[str, Any]) -> dict:
    cur = result["totals"].get("currency")
    t = result["totals"]
    digest: dict[str, Any] = {
        "period": f"{result['period']['start']} to {result['period']['end']} ({result['period']['days']} days)",
        "comparison": result["window"]["label"] or result["window"]["note"],
        "totals": {"gross": money(t["gross"], cur), "credits": money(t["credits"], cur), "net": money(t["net"], cur),
                   "daily_avg": money(t["daily_avg"], cur)},
        "top_movers": [{"name": m["name"], "previous": money(m["prev"], cur), "current": money(m["cur"], cur),
                        "change": money(m["change_abs"], cur, signed=True), "change_pct": mover_pct(m),
                        "evidence": [{"id": d["id"], "what": f"{_dim(d['dimension'])} {d['value']}",
                                      "change": money(d["delta"], cur, signed=True),
                                      "share_of_service_change": pct(d["share_pct"], signed=False)
                                      if d["share_pct"] is not None else "n/a"} for d in m["evidence"]]}
                       for m in result["top_movers"]],
        "anomalies": [{"id": f"a{i}", "dates": _dates(a), "service": a["service"], "value": money(a["value"], cur),
                       "trailing_mean": money(a["mean"], cur), "sigma": sigma(a["sigma"])}
                      for i, a in enumerate(result["anomalies"], start=1)],
        "waste_patterns": [{"id": w["id"], "title": w["title"], "cost_in_period": money(w["cost"], cur),
                            "estimate_per_month": money(w["est_monthly"], cur) if w["est_monthly"] else "not quantified",
                            "assumption": w["assumption"]} for w in result["waste_patterns"]],
    }
    u = result.get("untagged")
    digest["untagged"] = ({"tag": u["tag"], "cost": money(u["cost"], cur), "share": pct(u["share_pct"], signed=False)}
                          if u else "not computed: no tag column")
    b = t.get("budget")
    if b:
        digest["budget"] = {"month": b["month"], "budget": money(b["amount"], cur),
                            "spent_to_date": money(b["spent_to_date"], cur), "days_elapsed": str(b["days_elapsed"]),
                            "projected": money(b["projected"], cur),
                            "projected_vs_budget": pct(b["projected_vs_budget_pct"]), "note": b["label"]}
    return digest


def evidence_ids(digest: Mapping[str, Any]) -> set[str]:
    ids = {e["id"] for m in digest.get("top_movers", []) for e in m["evidence"]}
    ids |= {a["id"] for a in digest.get("anomalies", [])}
    ids |= {w["id"] for w in digest.get("waste_patterns", [])}
    return ids


def strings(value: Any) -> list[str]:
    """Every string in the digest (keys excluded)."""
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [str(value)] if value is not None else []


def _dim(dimension: str) -> str:
    return f"tag {dimension[4:]}" if dimension.startswith("tag:") else dimension.replace("_", " ")


def _dates(a: Mapping[str, Any]) -> str:
    return a["date_start"] if a["date_start"] == a["date_end"] else f"{a['date_start']} to {a['date_end']}"
