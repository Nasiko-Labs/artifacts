"""Pure: the fixed idle rules, one per resource kind, and the monthly cost of what a rule flags.

Every threshold is a constant here. A rule that needs a field the paste does not have does not guess:
the resource is "not evaluated" for that rule, with the missing field named, and is never flagged.
"older than N days" from the user replaces each rule's minimum idle days / age.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pricing
from inventory import Resource

UNATTACHED_VOLUME_MIN_IDLE_DAYS = 14
IDLE_INSTANCE_MAX_CPU = 5.0                     # percent, 14-day average, strictly below
IDLE_INSTANCE_MAX_NETWORK_PER_DAY = 5_000_000   # bytes per day, strictly below
METRICS_WINDOW_DAYS = 14
STOPPED_INSTANCE_MIN_DAYS = 30
ORPHANED_SNAPSHOT_MIN_AGE_DAYS = 30
UNASSOCIATED_IP_MIN_AGE_DAYS = 7
IDLE_LOAD_BALANCER_MIN_AGE_DAYS = 14
IDLE_NAT_GATEWAY_MIN_AGE_DAYS = 14
IDLE_NAT_GATEWAY_MAX_BYTES_14D = 1_000_000      # bytes over 14 days, strictly below

RULE_KINDS = ("unattached_volume", "idle_instance", "stopped_instance_with_storage", "orphaned_snapshot",
              "unassociated_ip", "idle_load_balancer", "idle_nat_gateway")
LABELS = {"unattached_volume": "unattached volume", "idle_instance": "idle instance",
          "stopped_instance_with_storage": "stopped instance with storage", "orphaned_snapshot": "orphaned snapshot",
          "unassociated_ip": "unassociated IP", "idle_load_balancer": "idle load balancer",
          "idle_nat_gateway": "idle NAT gateway"}
_UNASSOCIATED_STATES = frozenset({"unassociated", "available", "unattached", "idle"})
_ASSOCIATED_STATES = frozenset({"associated", "in-use", "in_use", "attached"})


@dataclass(frozen=True)
class Finding:
    resource_id: str
    kind: str              # one of RULE_KINDS
    rule: str              # what was checked, in words, with the values
    confidence: str        # high | medium
    idle_days: int
    idle_basis: str        # "last activity" | "state change" | "creation" | "metrics window"
    monthly_cost: Decimal | None
    cost_basis: str        # "reported" | pricing.ESTIMATED_BASIS | "unknown"
    currency: str
    detail: str            # "500 GB gp3", "t3.large"


@dataclass(frozen=True)
class Verdict:
    """A flagged finding, or the rule that could not be evaluated and the field it was missing."""

    finding: Finding | None = None
    rule: str = ""
    missing: str = ""


def idle_since(r: Resource) -> tuple[date | None, str]:
    """Last activity, else the last state change, else creation (shown as "idle days from creation")."""
    for value, basis in ((r.last_activity, "last activity"), (r.state_changed, "state change"),
                         (r.created, "creation")):
        if value is not None:
            return value, basis
    return None, ""


def evaluate(r: Resource, today: date, older_than: int | None = None,
             volumes: Mapping[str, Resource] | None = None) -> Verdict | None:
    """None when no rule applies (in use, running with load, too new, a kind with no rule)."""
    volumes = volumes or {}
    if r.kind == "volume":
        return _volume(r, today, older_than)
    if r.kind == "instance":
        if not r.state:
            return Verdict(rule="idle_instance", missing="state")
        if r.state == "running":
            return _running(r, today, older_than)
        return _stopped(r, today, older_than, volumes) if r.state == "stopped" else None
    if r.kind == "snapshot":
        return _snapshot(r, today, older_than)
    if r.kind == "address":
        return _address(r, today, older_than)
    if r.kind == "load_balancer":
        return _load_balancer(r, today, older_than)
    if r.kind == "nat_gateway":
        return _nat(r, today, older_than)
    return None


def _found(r: Resource, kind: str, rule: str, confidence: str, days: int, basis: str,
           estimate: tuple[Decimal | None, str]) -> Verdict:
    cost, detail = estimate
    if r.monthly_cost is not None:
        cost_value, cost_basis, currency = pricing.cents(r.monthly_cost), pricing.REPORTED_BASIS, r.currency or "USD"
    elif cost is not None:
        cost_value, cost_basis, currency = cost, pricing.ESTIMATED_BASIS, pricing.CURRENCY
    else:
        cost_value, cost_basis, currency = None, "unknown", pricing.CURRENCY
    return Verdict(Finding(r.id, kind, rule, confidence, days, basis, cost_value, cost_basis, currency, detail))


def _volume_estimate(r: Resource) -> tuple[Decimal | None, str]:
    vtype = r.volume_type or f"{pricing.DEFAULT_VOLUME_TYPE}, type not given"
    size = f"{r.size_gb:g} GB " if r.size_gb is not None else "size unknown, "
    return pricing.volume_monthly(r.size_gb, r.volume_type), f"{size}{vtype}"


def _volume(r: Resource, today: date, older_than: int | None) -> Verdict | None:
    kind = "unattached_volume"
    if not r.state:
        return Verdict(rule=kind, missing="state")
    if r.state != "available" or r.attached is True:
        return None
    since, basis = idle_since(r)
    if since is None:
        return Verdict(rule=kind, missing="created")
    days = (today - since).days
    if days < (older_than or UNATTACHED_VOLUME_MIN_IDLE_DAYS):
        return None
    return _found(r, kind, "state available, no attachments", "high", days, basis, _volume_estimate(r))


def _running(r: Resource, today: date, older_than: int | None) -> Verdict | None:
    kind = "idle_instance"
    if r.cpu_avg is None:
        return Verdict(rule=kind, missing="cpu_avg_14d")
    window = older_than or METRICS_WINDOW_DAYS
    if r.created is not None and (today - r.created).days < window:
        return None
    if r.cpu_avg >= IDLE_INSTANCE_MAX_CPU:
        return None
    net = r.network_bytes_per_day
    if net is not None and net >= IDLE_INSTANCE_MAX_NETWORK_PER_DAY:
        return None
    rule = f"running, cpu {r.cpu_avg:g}% avg 14 d" + (f", network {net:,.0f} bytes/day" if net is not None
                                                      else ", network not given")
    estimate = (pricing.instance_monthly(r.instance_type), r.instance_type or "type not given")
    return _found(r, kind, rule, "high" if net is not None else "medium", METRICS_WINDOW_DAYS, "metrics window",
                  estimate)


def _stopped(r: Resource, today: date, older_than: int | None, volumes: Mapping[str, Resource]) -> Verdict | None:
    kind = "stopped_instance_with_storage"
    since, basis = idle_since(r)
    if since is None:
        return Verdict(rule=kind, missing="stopped since")
    days = (today - since).days
    if days < (older_than or STOPPED_INSTANCE_MIN_DAYS):
        return None
    attached = [volumes[v] for v in r.volume_ids if v in volumes]
    if attached:
        costs = [_volume_estimate(v)[0] for v in attached]
        total = sum(costs, Decimal(0)) if all(c is not None for c in costs) else None
        size = sum(v.size_gb or 0 for v in attached)
        estimate = (total, f"{len(attached)} volume(s), {size:g} GB")
    elif r.volume_ids:
        estimate = (None, f"{len(r.volume_ids)} volume(s), sizes not pasted")
    elif r.size_gb is None:
        return Verdict(rule=kind, missing="attached volumes")
    elif r.size_gb <= 0:
        return None
    else:
        estimate = _volume_estimate(r)
    rule = "stopped, storage still attached (cost counted for the volumes)"
    return _found(r, kind, rule, "medium", days, basis, estimate)


def _aged(r: Resource, today: date, kind: str, minimum: int) -> tuple[Verdict | None, int]:
    """(a not-evaluated verdict, 0) when there is no creation date, else (None, age in days)."""
    if r.created is None:
        return Verdict(rule=kind, missing="created"), 0
    return None, (today - r.created).days


def _snapshot(r: Resource, today: date, older_than: int | None) -> Verdict | None:
    kind = "orphaned_snapshot"
    if r.source_exists is None:
        return Verdict(rule=kind, missing="source_exists")
    if r.source_exists or r.referenced_by_ami:
        return None
    missing, age = _aged(r, today, kind, ORPHANED_SNAPSHOT_MIN_AGE_DAYS)
    if missing or age < (older_than or ORPHANED_SNAPSHOT_MIN_AGE_DAYS):
        return missing
    confidence = "high" if r.referenced_by_ami is False else "medium"
    rule = "source volume gone, " + ("not used by an image" if r.referenced_by_ami is False
                                     else "image use not given")
    size = f"{r.size_gb:g} GB" if r.size_gb is not None else "size unknown"
    return _found(r, kind, rule, confidence, age, "creation", (pricing.snapshot_monthly(r.size_gb), size))


def _address(r: Resource, today: date, older_than: int | None) -> Verdict | None:
    kind = "unassociated_ip"
    if r.attached is True or r.state in _ASSOCIATED_STATES:
        return None
    if r.attached is None and r.state not in _UNASSOCIATED_STATES:
        return Verdict(rule=kind, missing="association")
    missing, age = _aged(r, today, kind, UNASSOCIATED_IP_MIN_AGE_DAYS)
    if missing or age < (older_than or UNASSOCIATED_IP_MIN_AGE_DAYS):
        return missing
    return _found(r, kind, "allocated, not associated", "high", age, "creation", (pricing.IP_MONTHLY, "public IPv4"))


def _load_balancer(r: Resource, today: date, older_than: int | None) -> Verdict | None:
    kind = "idle_load_balancer"
    if r.request_count_14d is None and r.healthy_targets is None:
        return Verdict(rule=kind, missing="request_count_14d")
    no_requests = r.request_count_14d == 0
    no_targets = r.healthy_targets == 0
    if not (no_requests or no_targets):
        return None
    missing, age = _aged(r, today, kind, IDLE_LOAD_BALANCER_MIN_AGE_DAYS)
    if missing or age < (older_than or IDLE_LOAD_BALANCER_MIN_AGE_DAYS):
        return missing
    rule = "no requests in 14 d" if no_requests else "no healthy targets"
    return _found(r, kind, rule, "medium", age, "creation", (pricing.LOAD_BALANCER_MONTHLY, "hourly charge only"))


def _nat(r: Resource, today: date, older_than: int | None) -> Verdict | None:
    kind = "idle_nat_gateway"
    if r.state in ("deleted", "deleting", "failed"):
        return None
    if r.network_bytes_14d is None:
        return Verdict(rule=kind, missing="network_bytes_14d")
    if r.network_bytes_14d >= IDLE_NAT_GATEWAY_MAX_BYTES_14D:
        return None
    missing, age = _aged(r, today, kind, IDLE_NAT_GATEWAY_MIN_AGE_DAYS)
    if missing or age < (older_than or IDLE_NAT_GATEWAY_MIN_AGE_DAYS):
        return missing
    rule = f"{r.network_bytes_14d:,.0f} bytes in 14 d"
    return _found(r, kind, rule, "medium", age, "creation", (pricing.NAT_GATEWAY_MONTHLY, "hourly charge only"))
