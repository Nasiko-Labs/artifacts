"""Pure: the whole analysis in code. Usage + commitments + options -> the result dict (JSON-safe, checkpointed
for follow-ups). Scopes: `compute` plus the top 3 family/region groups by spend (when there are 2+ groups)."""

from __future__ import annotations

import actions
import commitments
import fmt
import optimise
import scenarios
from constants import (
    COMPUTE,
    DAILY_SAFETY,
    GAP_TRIM_SHARE,
    HIGH_CONFIDENCE_MAX_GAP_SHARE,
    HIGH_CONFIDENCE_MIN_DAYS,
    MIN_HOURS,
    TERMS,
    THREE_YEAR_MIN_DAYS,
    TOP_GROUPS,
)
from params import Params
from usage import Usage, groups_by_spend

SPAN_ASSUMPTION = "The analysed span is assumed to represent future usage."


def scopes_for(usage: Usage, params: Params) -> list[str]:
    groups = groups_by_spend(usage)
    if params.compute_only or len(groups) < 2:
        return [COMPUTE]
    return [COMPUTE, *groups[:TOP_GROUPS]]


def confidence(usage: Usage, params: Params) -> str:
    if usage.gap_share > GAP_TRIM_SHARE:
        return "low"
    if (usage.trimmed or usage.days < HIGH_CONFIDENCE_MIN_DAYS or usage.granularity == "daily"
            or not params.quotes or usage.gap_share > HIGH_CONFIDENCE_MAX_GAP_SHARE):
        return "medium"
    return "high"


def choose_term(sizings: dict[int, dict], days: float, forced: int | None) -> int | None:
    if forced in sizings and sizings[forced]["eligible"]:
        return forced
    three = sizings[3]
    if three["eligible"] and three["downside_monthly_saving"] > 0 and days >= THREE_YEAR_MIN_DAYS:
        return 3
    return 1 if sizings[1]["eligible"] else None


def _facts(usage: Usage, cover: float, chosen: dict | None) -> list[str]:
    facts = [f"{fmt.days(usage.days)} days of {usage.granularity} data",
             f"existing cover {fmt.money(cover)} $/hr on-demand equivalent"]
    if chosen:
        facts.append(f"break-even utilization {fmt.pct(chosen['break_even_utilization'])}")
    return facts


def recommend(scope: str, usage: Usage, cover: float, params: Params) -> dict:
    series = usage.series[scope]
    residual = [max(0.0, v - cover) for v in series]
    daily = usage.granularity == "daily"
    sizings = {y: scenarios.with_downside(optimise.size(residual, params.discounts()[y], y, daily,
                                                        params.min_utilization), residual) for y in TERMS}
    term = choose_term(sizings, usage.days, params.term)
    base = {"scope": scope, "existing_od_hourly": round(cover, 4), "confidence": confidence(usage, params),
            "terms": {str(y): s for y, s in sizings.items()}}
    if term is None:
        return {**base, "action": "no_commitment", "term_years": None, "risk": None,
                "reason": "the residual usage is too spiky: no level reaches the minimum utilization with a saving",
                "rationale_facts": _facts(usage, cover, None)}
    chosen = sizings[term]
    total = sum(series)
    covered = sum(min(v, cover + chosen["od_hourly"]) for v in series)
    keys = ("hourly_commit", "monthly_saving", "term_saving", "utilization", "break_even_utilization",
            "downside_monthly_saving", "downside_utilization", "risk")
    return {**base, "action": "buy_now", "term_years": term, **{k: chosen[k] for k in keys},
            "coverage_pct": round(covered / total, 4) if total else 0.0, "reason": None,
            "rationale_facts": _facts(usage, cover, chosen)}


def assumptions(usage: Usage, params: Params) -> list[str]:
    d = params.discounts()
    label = "from your quotes where given" if params.quotes else "illustrative assumptions, not pricing"
    out = [f"Discounts ({label}): 1y {fmt.pct(d[1])}, 3y {fmt.pct(d[3])}, no upfront.",
           f"Minimum utilization {fmt.pct(params.min_utilization)}.", SPAN_ASSUMPTION]
    if not params.quotes:
        out.append("Ask your provider for real quotes and tell me, for example, \"1y discount 28%\".")
    if usage.granularity == "daily":
        out.append(f"Daily data: each commit is multiplied by {DAILY_SAFETY:.2f} because daily averages hide "
                   "intra-day dips.")
    return out


def run(usage: Usage, parsed: commitments.Parsed, params: Params) -> dict:
    discounts = params.discounts()
    coverage = {"rows_read": usage.rows_read, "rows_used": usage.rows_used, "rows_dropped": usage.rows_dropped,
                "gap_hours": usage.gap_hours, "trimmed": usage.trimmed, "start": usage.start.date().isoformat(),
                "end": usage.end_date.isoformat(), "days": round(usage.days, 1), "granularity": usage.granularity,
                "commitments_read": len(parsed.items), "commitments_dropped": parsed.dropped}
    base = {"coverage": coverage, "params": params.to_dict(), "quotes": params.quotes,
            "discounts": {str(k): v for k, v in discounts.items()}, "min_utilization": params.min_utilization,
            "assumptions": assumptions(usage, params), "existing": []}
    if usage.hours < MIN_HOURS:
        rec = {"scope": COMPUTE, "action": "needs_more_data", "term_years": None, "risk": None,
               "confidence": "low", "reason": "fewer than 14 days of usage", "rationale_facts": []}
        return {**base, "needs_more_data": True, "recommendations": [rec], "notes": list(usage.notes)}
    held = commitments.portfolio(parsed, usage.start.date(), usage.end_date, usage.series.keys(), discounts)
    recs = [recommend(s, usage, held.cover(s), params) for s in scopes_for(usage, params)]
    bought = {r["scope"]: r["terms"][str(r["term_years"])]["od_hourly"] for r in recs if r["action"] == "buy_now"}
    return {**base, "needs_more_data": False, "recommendations": recs,
            "existing": actions.existing(usage, held, params.min_utilization, bought),
            "notes": [*usage.notes, *held.notes]}
