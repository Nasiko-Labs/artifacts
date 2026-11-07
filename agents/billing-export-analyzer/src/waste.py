"""Pure: fixed waste-pattern rules over usage type / service / SKU (case-insensitive substring). Each rule has
an id, a fixed text and a fixed assumption; an estimate (per month, linear over the period) only where the
rule can quantify it. Estimates are never summed into one "you will save" figure."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from stats import gross
from windows import Windows, in_window

DAYS_PER_MONTH = 30.0
NEEDS_USAGE_TYPE = ("W1", "W2", "W3", "W4", "W5", "W6", "W7")
TITLES = {
    "W1": "Previous-generation EBS volumes (gp2)", "W2": "NAT gateway data processing",
    "W3": "Load balancers with no traffic", "W4": "Inter-AZ / regional data transfer",
    "W5": "Log ingestion", "W6": "Public IPv4 addresses", "W7": "Snapshot storage growth",
    "W8": "No commitment discounts",
}


@dataclass(frozen=True)
class Pattern:
    id: str
    title: str
    text: str
    cost: float
    est_monthly: float | None
    assumption: str

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "text": self.text, "cost": self.cost,
                "est_monthly": self.est_monthly, "assumption": self.assumption}


def _match(series: pd.Series, pattern: str) -> pd.Series:
    return series.fillna("").astype(str).str.casefold().str.contains(pattern, regex=True)


def _monthly(cost: float, days: int) -> float:
    return cost / max(days, 1) * DAYS_PER_MONTH


def check(df: pd.DataFrame, windows: Windows) -> tuple[list[Pattern], list[str]]:
    """(patterns found, rule ids not checked for lack of a column)."""
    g = gross(df)
    total = float(g["cost"].sum())
    found: list[Pattern] = []
    if "usage_type" not in g:
        missing = [f"{r} ({TITLES[r]}): needs a usage-type column" for r in NEEDS_USAGE_TYPE]
        if "line_type" not in g:
            return [], [*missing, f"W8 ({TITLES['W8']}): needs a line-type or usage-type column"]
        return _commitments(g, df, total), missing
    usage = g["usage_type"]
    days = windows.days

    def cost_of(pattern: str, where: pd.Series | None = None) -> float:
        mask = _match(usage, pattern)
        return float(g[mask if where is None else mask & where]["cost"].sum())

    gp2 = cost_of(r"volumeusage\.gp2")
    if gp2 > 0:
        found.append(Pattern("W1", TITLES["W1"], "gp2 volumes found; gp3 is cheaper per GB for the same size.", gp2,
                             0.20 * _monthly(gp2, days), "assumes gp3 list price is ~20% lower per GB; verify"))
    nat = _match(usage, r"natgateway-bytes")
    if nat.any():
        nat_cost = float(g[nat]["cost"].sum())
        services = g[nat]["service"].unique()
        svc_total = float(g[g["service"].isin(services)]["cost"].sum())
        if svc_total and nat_cost / svc_total >= 0.15:
            found.append(Pattern("W2", TITLES["W2"], "NAT data processing is a large share; consider VPC endpoints "
                                 "for S3/DynamoDB.", nat_cost, None, "not quantified: depends on which traffic moves"))
    lb_hours, lcu = cost_of(r"loadbalancerusage"), cost_of(r"lcuusage")
    if lb_hours > 0 and lcu <= max(0.01 * lb_hours, 0.01):
        found.append(Pattern("W3", TITLES["W3"], "Load balancer hours are billed with no capacity units used.",
                             lb_hours, _monthly(lb_hours, days), "assumes no traffic; verify"))
    _share_rule(found, "W4", cost_of(r"datatransfer-regional-bytes"), total, 0.05,
                "Inter-AZ / regional transfer is a notable share; check cross-AZ chatter and placement.")
    logs = g["service"].fillna("").str.casefold().str.contains("log") | _match(usage, "log")
    _share_rule(found, "W5", cost_of(r"datapayload|dataprocessing-bytes", logs), total, 0.05,
                "Log ingestion is a notable share: review log retention and verbosity.")
    _share_rule(found, "W6", cost_of(r"publicipv4"), total, 0.01,
                "Public IPv4 addresses are billed hourly; release unused ones.")
    if windows.comparable and not windows.monthly:
        snap = _match(usage, r"snapshotusage")
        cur = float(g[snap & in_window(g["date"], windows.cur)]["cost"].sum())
        prev = float(g[snap & in_window(g["date"], windows.prev)]["cost"].sum())
        if prev > 0 and (cur - prev) / prev >= 0.20:
            found.append(Pattern("W7", TITLES["W7"], "Snapshot storage grew by 20% or more window over window; "
                                 "check snapshot retention.", cur, None, "not quantified"))
    return found + _commitments(g, df, total), []


def _share_rule(found: list[Pattern], rule: str, cost: float, total: float, share: float, text: str) -> None:
    if total and cost / total >= share:
        found.append(Pattern(rule, TITLES[rule], text, cost, None, "not quantified"))


_EC2 = re.compile(r"ec2|elastic compute|compute engine|virtual machines")


def _commitments(g: pd.DataFrame, df: pd.DataFrame, total: float) -> list[Pattern]:
    compute = g["service"].fillna("").str.casefold().str.contains(_EC2.pattern, regex=True)
    compute_cost = float(g[compute]["cost"].sum())
    if not total or compute_cost / total < 0.10:
        return []
    marks = [df[c] for c in ("line_type", "usage_type") if c in df]
    if any(_match(m, r"savingsplan|savings plan|reserved|\bri\b").any() for m in marks):
        return []
    return [Pattern("W8", TITLES["W8"], "No commitment discounts seen; check commitment coverage.", compute_cost,
                    None, "coverage is unknown from this export")]
