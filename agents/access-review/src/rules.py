"""Pure: the review rules over parsed export rows, the roster and the code-host organization facts. All
dates, day counts and counts are computed here against the turn clock; nothing is asked of a model."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import findings as fd
from findings import Finding
from identity import Match, Roster, is_service_account, match_login, match_row
from parse import AccessRow, RosterRow
from policy import Policy
from records import OrgData

PRIVILEGED_ROLE = re.compile(r"admin|owner|root|superuser|\bprod\b|production|write-all|full access|editor",
                             re.IGNORECASE)


@dataclass(frozen=True)
class Review:
    findings: tuple[Finding, ...]
    holders: int            # distinct access holders reviewed (export + code host)
    privileged: int         # distinct privileged holders
    matched: int            # holders matched to the roster (any tier)
    weak: int               # holders matched by name only
    on_leave: int           # roster "leave" people who hold access (treated as active)
    disabled_rows: int      # export rows already suspended/disabled (skipped)


def is_privileged(role: str) -> bool:
    return bool(PRIVILEGED_ROLE.search(role or ""))


def days_between(earlier: str, today: date) -> int | None:
    return (today - date.fromisoformat(earlier)).days if earlier else None


def evaluate(access: Sequence[AccessRow], roster: Roster, org: OrgData | None, policy: Policy, today: date,
             mappings: Mapping[str, str]) -> Review:
    out: list[Finding] = []
    holders: dict[str, tuple[bool, Match]] = {}   # key → (privileged, match)
    disabled = 0
    for row in access:
        if row.disabled:
            disabled += 1
            continue
        m = match_row(roster, row.email, row.name)
        key = row.email or row.name.casefold()
        prior = holders.get(key)
        holders = {**holders, key: ((prior[0] if prior else False) or is_privileged(row.role), m)}
        out.extend(_row_findings(row, m, roster, policy, today))
    if org is not None:
        found, org_holders = _org_findings(org, roster, policy, mappings)
        out.extend(found)
        holders = {**holders, **{k: v for k, v in org_holders.items() if k not in holders}}
    on_leave = {m.person.email or m.person.name for _, m in holders.values() if m.person and m.person.status == "leave"}
    return Review(fd.ordered(out), len(holders), sum(1 for p, _ in holders.values() if p),
                  sum(1 for _, m in holders.values() if m.person), sum(1 for _, m in holders.values() if m.weak),
                  len(on_leave), disabled)


def _label(person: RosterRow | None, *fallbacks: str) -> str:
    return (person.name if person and person.name else "") or next((f for f in fallbacks if f), "unknown")


def _roster_note(person: RosterRow) -> str:
    return f"roster: {person.status}" + (f" {person.end_date}" if person.end_date else "")


def _row_findings(row: AccessRow, m: Match, roster: Roster, policy: Policy, today: date) -> list[Finding]:
    person = m.person
    priv = is_privileged(row.role)
    days = days_between(row.last_sign_in, today)
    seen = f"last sign-in {row.last_sign_in}" if row.last_sign_in else ("never signed in" if row.never_signed_in
                                                                       else "last sign-in unknown")
    base = {"person": _label(person, row.name, row.email), "email": row.email, "system": row.system or "unknown",
            "role": row.role or "unknown", "weak": m.weak, "privileged": priv, "days_since": days}
    evidence = f"export row {row.row} · {seen}"
    if is_service_account(row.email.split("@")[0], row.name):
        return [fd.make("service_account_review", **{**base, "weak": False}, evidence=evidence)]
    if roster.present and person and person.status == "terminated":
        return [fd.make("terminated_has_access", **base, evidence=f"{_roster_note(person)} · {evidence}")]
    if roster.present and not person:
        return [fd.make("not_on_roster", **base, candidate=bool(row.email),
                        evidence=f"{evidence} · " + ("no roster row with this email" if row.email
                                                      else "no email in the export and no roster name match"))]
    out = []
    dormant_days = days if days is not None else (
        days_between(row.created, today) if row.never_signed_in and row.created else None)
    if dormant_days is not None and dormant_days >= policy.dormant_days:
        detail = evidence if days is not None else f"{evidence} · created {row.created}"
        out.append(fd.make("dormant", **base, candidate=priv, evidence=detail, days=dormant_days,
                           limit=policy.dormant_days))
    if person and priv:
        out.extend(_privilege_checks(person, base, evidence, policy))
    return out


def _privilege_checks(person: RosterRow, base: dict, evidence: str, policy: Policy) -> list[Finding]:
    out = []
    title = person.title or "no title"
    if person.title and not policy.title_allowed(person.title):
        out.append(fd.make("privileged_without_role_need", **base, evidence=f"roster title: {title} · {evidence}"))
    missing = " and ".join(x for x, v in (("manager", person.manager), ("title", person.title)) if not v)
    if missing:
        out.append(fd.make("no_manager_or_unknown_title", **base, evidence=f"roster row {person.row} · {evidence}",
                           missing=missing))
    return out


def _org_findings(org: OrgData, roster: Roster, policy: Policy,
                  mappings: Mapping[str, str]) -> tuple[list[Finding], dict[str, tuple[bool, Match]]]:
    out: list[Finding] = []
    holders: dict[str, tuple[bool, Match]] = {}
    system = f"code-host org {org.org}"
    owners = set(org.owners)
    for login in org.members:
        admin_on = org.repo_admins.get(login, ())
        priv = login in owners or bool(admin_on)
        role = "owner" if login in owners else ("admin on " + ", ".join(r for r, _ in admin_on[:3]) if admin_on
                                                else "member")
        profile = org.profiles.get(login)
        m = match_login(roster, login, mappings, profile)
        holders = {**holders, (m.email or f"@{login}"): (priv, m)}
        base = {"person": _label(m.person, profile.name if profile else "", login), "email": m.email, "system": system, "role": role,
                "weak": m.weak, "privileged": priv}
        how = f"login {login}" + (f" · matched by {m.how}" if m.how else "")
        if is_service_account(login):
            out.append(fd.make("service_account_review", **{**base, "weak": False}, evidence=how))
            continue
        if not roster.present:
            continue
        if m.person and m.person.status == "terminated":
            out.append(fd.make("terminated_has_access", **base, evidence=f"{_roster_note(m.person)} · {how}"))
        elif not m.person and m.email:
            out.append(fd.make("not_on_roster", **base, evidence=f"{how} · no roster row with this email"))
        elif not m.person:
            out.append(fd.make("unmatched_identity", **base, evidence=how))
        elif priv:
            out.extend(_privilege_checks(m.person, base, how, policy))
    if len(owners) > policy.max_owners:
        out.append(fd.make("excess_owners", person=f"{len(owners)} owners", email="", system=system, role="owner",
                           evidence="owners: " + ", ".join(sorted(owners)), privileged=True, count=len(owners),
                           limit=policy.max_owners))
    for login in org.outside:
        for repo, perm in org.repo_admins.get(login, ()):
            out.append(fd.make("outside_collaborator_admin", person=login, email="", system=f"repository {repo}",
                               role=perm, evidence=f"outside collaborator {login} · {perm} on {repo}",
                               privileged=True, perm=perm))
        holders = {**holders, f"@{login}": (bool(org.repo_admins.get(login)), Match(None))}
    return out, holders
