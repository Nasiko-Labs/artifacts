"""Pure: downtime (DT01-DT05) and exposure (EX01) rules per change, and the plan-wide scope rules (SC01)."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator, Sequence

import resources
import rules_exposure
from changes import Change, Finding, changed, make, turned_false

WILDCARD = re.compile(r'\\?"?\b(Action|Resource|Principal|actions|resources)\b\\?"?\s*[:=]\s*\[?\s*\\?"\*\\?"')
DB_RESTART_ATTRS = ("instance_class", "engine_version", "allocated_storage")
DEPENDENTS_FOR_HIGH = 3
MANY_MODULES = 4
MANY_SERVICES = 6
LARGE_PLAN = 50


def per_change(change: Change, plan_changes: Sequence[Change]) -> Iterator[Finding]:
    yield from _dt01_recreate(change)
    yield from _dt02_restart(change)
    yield from _dt03_network(change, plan_changes)
    yield from _dt04_dns(change)
    yield from _dt05_iam(change)
    yield from rules_exposure.ex01_exposure(change)


def _dt01_recreate(c: Change) -> Iterator[Finding]:
    if c.action != "replace" or not resources.matches(c.resource_type, resources.COMPUTE_DOWNTIME):
        return
    if c.cbd:
        yield make("DT01", "low", c, "replaced, but the new one is created before the old one is destroyed.",
                   f"Can {c.address} run two copies at once during the swap?")
    else:
        yield make("DT01", "medium", c, "replaced, destroy first: expect downtime while it is recreated.",
                   f"Is downtime for {c.address} acceptable, or should it use create_before_destroy?")


def _dt02_restart(c: Change) -> Iterator[Finding]:
    if c.action != "update":
        return
    rtype = c.resource_type
    if rtype == "aws_db_instance":
        apply_now = c.attr("apply_immediately")
        immediate = apply_now is not None and apply_now.after == "true"
        for a in c.attrs_named(*DB_RESTART_ATTRS):
            if changed(a):
                when = "now (apply_immediately = true)" if immediate else "in the next maintenance window"
                yield make("DT02", "high" if immediate else "medium", c, f"{a.name} changes: the database restarts "
                           f"{when}.", "When is the restart window, and is the app ready for it?", a)
        if turned_false(c.attr("multi_az")):
            yield make("DT02", "high", c, "multi_az is turned off: no standby during failures or maintenance.",
                       "Why drop the standby?", c.attr("multi_az"))
        if changed(c.attr("port")):
            yield make("DT02", "medium", c, "the database port changes: clients must be updated at the same time.",
                       "Are all clients updated for the new port?", c.attr("port"))
    elif rtype == "google_sql_database_instance":
        tier = c.attr("settings.tier")
        if changed(tier):
            yield make("DT02", "medium", c, "the instance tier changes: the database restarts.",
                       "When is the restart window?", tier)
        avail = c.attr("settings.availability_type")
        if avail is not None and avail.before == "REGIONAL" and avail.after == "ZONAL":
            yield make("DT02", "high", c, "availability drops from REGIONAL to ZONAL: no failover replica.",
                       "Why drop regional availability?", avail)
    elif rtype.startswith("azurerm_"):
        for a in c.attrs_named("sku_name", "tier"):
            if changed(a):
                yield make("DT02", "medium", c, f"{a.name} changes: the service may restart or scale.",
                           "When is the restart window?", a)


def _dt03_network(c: Change, plan_changes: Sequence[Change]) -> Iterator[Finding]:
    if c.action != "replace" or c.resource_type not in resources.NETWORK:
        return
    others = sum(1 for o in plan_changes if o.address != c.address and o.module_path == c.module_path
                 and o.action not in ("noop", "read"))
    severity = "high" if others >= DEPENDENTS_FOR_HIGH else "medium"
    yield make("DT03", severity, c, f"a network primitive is replaced; {others} other change(s) in the same module may "
               "depend on it.", f"What loses connectivity while {c.address} is recreated?")


def _dt04_dns(c: Change) -> Iterator[Finding]:
    rtype = c.resource_type
    if c.action in ("replace", "delete") and ("route53_record" in rtype or "dns_record" in rtype):
        yield make("DT04", "medium", c, "a DNS record is replaced or removed: expect a propagation gap.",
                   f"Is a gap in resolving {c.address} acceptable?")
    elif rtype == "aws_acm_certificate" and c.action == "replace":
        yield make("DT04", "medium", c, "the certificate is replaced: validation must finish before listeners use it.",
                   "Is the new certificate validated before the old one goes?")
    elif c.action == "update" and changed(c.attr("certificate_arn")):
        yield make("DT04", "medium", c, "the listener certificate is swapped.", "Is the new certificate valid for "
                   "every hostname?", c.attr("certificate_arn"))


def _dt05_iam(c: Change) -> Iterator[Finding]:
    rtype = c.resource_type
    if rtype == "aws_iam_role" and c.action == "replace":
        yield make("DT05", "high", c, "the IAM role is replaced: anything assuming it loses access until it is back.",
                   "What uses this role, and can it tolerate the gap?")
    grants = ("iam" in rtype or "policy" in rtype) and c.action in ("create", "update", "replace")
    if grants and len(WILDCARD.findall(c.after_text)) > len(WILDCARD.findall(c.before_text)):
        yield make("DT05", "high", c, "a policy grants a wildcard (\"*\") action, resource or principal.",
                   f"Can the wildcard in {c.address} be narrowed?")
    if "policy_attachment" in rtype and c.action == "delete":
        yield make("DT05", "medium", c, "a policy attachment is removed: something may lose permissions.",
                   "What relied on this attachment?")


def plan_scope(changes: Sequence[Change], intent: str) -> list[Finding]:
    """SC01 (a) a wide plan for a stated intent and (d) a very large plan."""
    acting = [c for c in changes if c.action not in ("noop", "read")]
    if not acting:
        return []
    whole = Change(address="(whole plan)", resource_type="", action="plan", evidence=f"{len(acting)} changes")
    found: list[Finding] = []
    modules = Counter(c.module_path or "(root)" for c in acting)
    services = {resources.service(c.resource_type) for c in acting}
    if intent and (len(modules) >= MANY_MODULES or len(services) >= MANY_SERVICES):
        found.append(make("SC01", "medium", whole, f"the plan touches {len(modules)} modules and {len(services)} "
                          "services for one stated intent.", "Does everything here belong to this change?"))
    if len(acting) > LARGE_PLAN:
        found.append(make("SC01", "medium", whole, f"a large plan ({len(acting)} changes): consider splitting it.",
                          "Can this be split into smaller applies?"))
    return found
