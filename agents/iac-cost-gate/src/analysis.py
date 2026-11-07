"""Pure: parsed IaC + settings -> the full result (lines, totals, delta, violations, assumptions, verdict).
Rerun on every follow-up that changes a setting, with zero model calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import prices as p
import pricing
import verdict as vd
import waste

BASELINE = (f"AWS on-demand list prices as of {p.AS_OF}, Linux, shared tenancy, {p.HOURS_PER_MONTH} hours/month.",)


def _region(opts: Mapping[str, Any], parsed: Mapping[str, Any]) -> tuple[str, float, bool]:
    region = opts.get("region") or parsed.get("region") or p.DEFAULT_REGION
    mult = p.multiplier(region)
    return (region, mult, True) if mult is not None else (region, 1.0, False)


def _price_all(resources: Sequence[Mapping[str, Any]], mult: float, nat_gb: float) -> list[dict]:
    return [pricing.line(r, mult, nat_gb) for r in resources]


def _total(lines: Sequence[Mapping[str, Any]]) -> float:
    return round(sum(ln["monthly"] for ln in lines if ln["basis"] == "priced"), 2)


def run(parsed: Mapping[str, Any], opts: Mapping[str, Any], source: str = "") -> dict:
    region, mult, supported = _region(opts, parsed)
    resources = parsed["resources"]
    lines = _price_all(resources, mult, float(opts.get("nat_gb") or p.NAT_DEFAULT_GB))
    by_addr = {ln["address"]: ln for ln in lines}
    found = waste.check(resources, by_addr, parsed.get("default_tags") or {}, opts.get("env") or "",
                        opts.get("tag_groups") or waste.DEFAULT_TAG_GROUPS)
    kept = [v for v in found if not source or v["evidence"] in source or v["evidence"].casefold() in source.casefold()]
    kept.sort(key=lambda v: (v["severity"] != "medium", v["rule_id"], v["address"]))
    violations = [{"id": f"v{n}", **v} for n, v in enumerate(kept, start=1)]
    estimate = _total(lines)
    before = delta = None
    if parsed.get("before") is not None:
        before = _total(_price_all(parsed["before"], mult, float(opts.get("nat_gb") or p.NAT_DEFAULT_GB)))
        delta = round(estimate - before, 2)
    counts = {b: sum(1 for ln in lines if ln["basis"] == b) for b in ("priced", "usage_dependent", "unpriced", "free")}
    considered = counts["priced"] + counts["usage_dependent"] + counts["unpriced"]
    share = (counts["usage_dependent"] + counts["unpriced"]) / considered if considered else 0.0
    assumptions, guesses = _assumptions(parsed, lines, region, supported)
    verdict, reasons = vd.decide(estimate, delta, opts.get("limit"), violations, share)
    ordered = sorted(lines, key=lambda ln: (ln["basis"] != "priced", -ln["monthly"], ln["address"]))
    return {
        "format": parsed["format"], "region": region, "region_supported": supported, "limit": opts.get("limit"),
        "env": opts.get("env") or "", "nat_gb": float(opts.get("nat_gb") or p.NAT_DEFAULT_GB), "as_of": p.AS_OF,
        "estimated_monthly": estimate, "estimated_monthly_before": before, "delta": delta,
        "lines": [ln for ln in ordered if ln["basis"] != "free"], "violations": violations,
        "assumptions": assumptions, "guesses": guesses, "counts": counts, "not_estimated_share": round(share, 4),
        "unpriced": [{"address": ln["address"], "reason": "; ".join(ln["assumptions"]) or "not in my price table"}
                     for ln in lines if ln["basis"] == "unpriced"],
        "usage_dependent": [ln["address"] for ln in lines if ln["basis"] == "usage_dependent"],
        "verdict": verdict, "verdict_reasons": reasons, "notes": list(parsed.get("notes") or []),
        "dropped": len(found) - len(kept), "multiplied": parsed.get("multiplied", 0),
        "unresolved_counts": parsed.get("unresolved_counts", 0), "blocks_failed": parsed.get("blocks_failed", 0),
        "narrative": "", "narrative_source": "template", "fixes": {},
    }


def _assumptions(parsed, lines, region: str, supported: bool) -> tuple[list[str], int]:
    out = list(BASELINE)
    if not supported:
        out.append(f"Region {region} is not in my price table: priced as {p.DEFAULT_REGION}.")
    elif region != p.DEFAULT_REGION:
        out.append(f"Region {region}: {p.DEFAULT_REGION} prices x{p.REGION_MULTIPLIERS[region]:.2f}.")
    out += parsed.get("assumptions") or []
    guesses = len(parsed.get("assumptions") or [])
    for ln in lines:
        if ln["basis"] != "priced":
            continue
        for note in ln["assumptions"]:
            out.append(f"{ln['address']}: {note}.")
            guesses += 1
    return list(dict.fromkeys(out)), guesses
