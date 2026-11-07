"""Pure: waste rules T01–T04 (the ones with an estimate), the unit groups they read, and `find`, which runs every
rule and numbers the findings t1, t2, … (estimated first, largest first). T05–T08 live in waste_notes.py.

An estimate is for this export's period scaled to 30 days (`est_monthly_waste`), or None when the export has no
dates. Estimates are potential and never summed. No finding names a person (the user dimension is never used)."""

from __future__ import annotations

from collections.abc import Mapping

import fmt
import prices
import waste_notes
from aggregate import period
from frame import Frame
from units import units

MONTH_DAYS = 30
T01_MIN_SHARE_PCT = 5.0
T02_MIN_AVG_INPUT = 8_000
T02_MIN_IN_OUT_RATIO = 20.0
T02_TRIM_SHARE = 0.30
T03_MAX_CACHED_PCT = 10.0
T03_MIN_AVG_INPUT = 1_500
T03_MIN_REQUESTS = 100
T03_REPEATED_SHARE, T03_DISCOUNT = 0.5, 0.5
T04_MAX_AVG_INPUT, T04_MAX_AVG_OUTPUT = 500, 150
RULE_ORDER = ("T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08")


def monthly(amount: float | None, days: int | None) -> float | None:
    return None if amount is None or not days else amount * MONTH_DAYS / days


def finding(rule: str, title: str, unit: Mapping | None, evidence: str, est: float | None, days: int | None,
            assumption: str, hint: str, dimension: str = "", key: str = "", extra: dict | None = None) -> dict:
    return {"rule_id": rule, "title": title, "dimension": unit["dimension"] if unit else dimension,
            "key": unit["key"] if unit else key, "evidence": evidence, "est_in_export": est,
            "est_monthly_waste": monthly(est, days), "assumption": assumption, "hint": hint, **(extra or {})}


def t01_retries(frame: Frame, days: int | None) -> list[dict]:
    df = frame.df
    total = float(df["cost"].sum())
    retried = float(df.loc[df["failed"] == 1, "cost"].sum())
    share = retried / total * 100 if total else 0.0
    if share < T01_MIN_SHARE_PCT:
        return []
    req = float(df.loc[df["failed"] == 1, "requests"].sum())
    return [finding("T01", "retries and failed calls", None,
                    f"{fmt.money(retried)} ({fmt.pct(share)} of cost) went to {fmt.count(req)} failed or retried "
                    "requests", retried, days, "assumes the failure rate is steady",
                    "Find why calls fail or retry and add backoff or input validation before resending.",
                    "status", "failed or retried", {"share_pct": share})]


def t02_oversized(u: Mapping, days: int | None) -> list[dict]:
    ratio = u["input"] / u["output"] if u["output"] else float("inf")
    if u["avg_in"] < T02_MIN_AVG_INPUT or ratio < T02_MIN_IN_OUT_RATIO:
        return []
    est = prices.input_cost(u["price"], u["input"], u["cached"]) * T02_TRIM_SHARE if u["price"] else None
    return [finding("T02", "oversized prompts", u,
                    f"{u['label']} averages {fmt.count(u['avg_in'])} input / {fmt.count(u['avg_out'])} output tokens "
                    f"({fmt.ratio(ratio)}:1)", float(est) if est is not None else None, days,
                    "assumes prompts can be trimmed by 30%; unverified",
                    "Trim the prompt: retrieve fewer chunks or summarise context before sending it.")]


def t03_cache(u: Mapping, days: int | None) -> list[dict]:
    p = u["price"]
    cached_pct = u["cached"] / u["input"] * 100 if u["input"] else 0.0
    if (p is None or p.cached_input >= p.input or cached_pct >= T03_MAX_CACHED_PCT
            or u["avg_in"] < T03_MIN_AVG_INPUT or u["requests"] < T03_MIN_REQUESTS):
        return []
    est = float(prices.input_cost(p, u["input"], u["cached"])) * T03_REPEATED_SHARE * T03_DISCOUNT
    return [finding("T03", "cache underuse", u,
                    f"{u['label']}: {fmt.pct(cached_pct)} of {fmt.count(u['input'])} input tokens were cached over "
                    f"{fmt.count(u['requests'])} requests (avg {fmt.count(u['avg_in'])} input)", est, days,
                    "assumes half the input is repeated and cached input is 50% cheaper; verify",
                    "Put the stable part of the prompt first so the provider can cache it.")]


def t04_overkill(u: Mapping, days: int | None, table: Mapping) -> list[dict]:
    p = u["price"]
    if p is None or p.tier != prices.PREMIUM or u["avg_in"] >= T04_MAX_AVG_INPUT or u["avg_out"] >= T04_MAX_AVG_OUTPUT:
        return []
    small = table.get(p.smaller) if p.smaller else None
    est = None
    if small is not None:
        est = max(u["cost"] - float(prices.token_cost(small, u["input"], u["output"], u["cached"])), 0.0)
    return [finding("T04", "model overkill", u,
                    f"{u['label']} averages {fmt.count(u['avg_in'])} in / {fmt.count(u['avg_out'])} out tokens "
                    f"over {fmt.count(u['requests'])} requests" + (f"; {p.smaller} would cost less" if small else ""),
                    est, days, "assumes the smaller model's quality is acceptable; test first",
                    f"Route these small calls to {p.smaller or 'a smaller model'} after an A/B quality check.",
                    extra={"model": u["model"], "smaller": p.smaller})]


def find(frame: Frame) -> tuple[list[dict], list[str]]:
    """(findings with ids, not-checked notes)."""
    p = period(frame.df)
    days = p["days"] if p else None
    table = prices.table_with(frame.meta.get("price_table"))
    notes: list[str] = []
    found = t01_retries(frame, days) if frame.has_status else []
    if not frame.has_status:
        notes.append("T01 retries: not checked (no status or attempt column)")
    for u in units(frame):
        found += t02_oversized(u, days) + t03_cache(u, days) + t04_overkill(u, days, table)
    found += waste_notes.all_notes(frame)
    found.sort(key=lambda f: (f["est_in_export"] is None, -(f["est_in_export"] or 0.0), RULE_ORDER.index(f["rule_id"])))
    return [{"id": f"t{i}", **f} for i, f in enumerate(found, start=1)], notes


def by_rule(findings: list[dict], rule: str) -> list[dict]:
    return [f for f in findings if f["rule_id"] == rule]

