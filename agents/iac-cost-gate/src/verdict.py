"""Pure: the verdict (pass | warn | fail), `confidence` and `needs_human`, all computed here from the run's
numbers and checks, never taken from the model. Without a limit the verdict never fails on cost alone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

WASTE_FAIL_SHARE = 0.20
NOT_ESTIMATED_WARN = 0.30
NOT_ESTIMATED_LOW = 0.50
UNRESOLVED_COUNTS_LOW = 0.30
MAX_GUESSES_HIGH = 5


def money(value: float, signed: bool = False) -> str:
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def decide(estimate: float, delta: float | None, limit: float | None, violations: Sequence[Mapping[str, Any]],
           not_estimated_share: float) -> tuple[str, list[str]]:
    fails, warns = [], []
    gated = delta if delta is not None else estimate
    if limit is not None and gated > limit:
        what = "monthly delta" if delta is not None else "estimate"
        fails.append(f"the {what} {money(gated, delta is not None)} is over the limit {money(limit)}")
    idle = sum(v["est_monthly_saving"] or 0.0 for v in violations if v["rule_id"] in ("W02", "W11"))
    if estimate > 0 and idle > WASTE_FAIL_SHARE * estimate:
        fails.append(f"unattached IPs and volumes cost {money(idle)}, over 20% of the estimate")
    mediums = sorted({v["rule_id"] for v in violations if v["severity"] == "medium"})
    if mediums:
        warns.append(f"medium violations: {', '.join(mediums)}")
    if not_estimated_share > NOT_ESTIMATED_WARN:
        warns.append(f"{not_estimated_share:.0%} of resources are unpriced or usage-dependent")
    if any(v["rule_id"] == "W12" for v in violations) and "W12" not in mediums:
        warns.append("cost-allocation tags missing (W12)")
    if fails:
        return "fail", fails
    return ("warn", warns) if warns else ("pass", [])


def confidence(result: Mapping[str, Any]) -> str:
    multiplied = result["multiplied"]
    unresolved_share = result["unresolved_counts"] / multiplied if multiplied else 0.0
    if result["blocks_failed"] or result["not_estimated_share"] > NOT_ESTIMATED_LOW \
            or unresolved_share > UNRESOLVED_COUNTS_LOW:
        return "low"
    if (not result["region_supported"] or result["guesses"] > MAX_GUESSES_HIGH
            or result.get("narrative_source") != "model" or result.get("dropped")):
        return "medium"
    return "high"


def needs_human(result: Mapping[str, Any], level: str) -> bool:
    if level == "low" or result["verdict"] == "fail":
        return True
    return result["verdict"] == "warn" and result["not_estimated_share"] > NOT_ESTIMATED_LOW
