"""Pure: DL12-DL14, the force-delete and retention rules (registry, DNS zone, IAM user; Azure vaults, storage,
SQL and Cosmos DB retention)."""

from __future__ import annotations

from collections.abc import Iterator

from changes import Attr, Change, Finding, make, number, turned_false, turned_true


def dl12_force(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    rules = {"aws_ecr_repository": ("force_delete", "medium", "deleting the repository also deletes its images."),
             "aws_route53_zone": ("force_destroy", "medium", "destroying the zone also deletes all its records."),
             "aws_iam_user": ("force_destroy", "low", "destroying the user also removes its keys and logins.")}
    if rtype in rules:
        name, severity, reason = rules[rtype]
        a = c.attr(name)
        if turned_true(a):
            yield make("DL12", severity, c, f"{name} is set: {reason}", f"Is {name} on {c.address} intended?", a)


def dl13_14_azure(c: Change, rtype: str, act: str) -> Iterator[Finding]:
    if not rtype.startswith("azurerm_") or act != "update":
        return
    if rtype == "azurerm_key_vault" and turned_false(c.attr("purge_protection_enabled")):
        a = c.attr("purge_protection_enabled")
        yield make("DL13", "high", c, "purge protection is turned off.", "Why turn purge protection off?", a)
    checks = {"azurerm_key_vault": ("soft_delete_retention_days", "high", "DL13"),
              "azurerm_storage_account": ("delete_retention_policy.days", "high", "DL13"),
              "azurerm_mssql_database": ("short_term_retention_policy.retention_days", "medium", "DL13"),
              "azurerm_cosmosdb_account": ("retention_in_hours", "medium", "DL14")}
    if rtype in checks:
        suffix, severity, rule = checks[rtype]
        for a in c.attrs:
            if a.name.endswith(suffix) and _lowered(a):
                yield make(rule, severity, c, f"{a.name} is lowered or removed: deleted data is kept for less time.",
                           "Is the shorter retention intended?", a)


def _lowered(a: Attr) -> bool:
    before, after = number(a.before), number(a.after)
    return before is not None and (a.after is None or (after is not None and after < before))
