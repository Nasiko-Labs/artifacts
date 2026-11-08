"""Pure: the finding record, the rule table (kind → severity, removal candidate, rule id and sentence)
and the ordering. Every finding states its evidence and the rule that produced it."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

SEVERITY_ORDER = ("critical", "high", "medium", "low")

# kind → (severity, removal candidate, rule id, rule text)
RULES: dict[str, tuple[str, bool, str, str]] = {
    "terminated_has_access": ("critical", True, "R1", "terminated per the roster and access is still present"),
    "not_on_roster": ("high", True, "R2", "holds access and is not on the supplied roster"),
    "dormant": ("medium", False, "R3", "no sign-in for {days} days (policy: dormant after {limit} days)"),
    "privileged_without_role_need": ("medium", False, "R4",
                                     "privileged role while the roster title is not in the allowed admin titles"),
    "excess_owners": ("medium", False, "R5", "{count} organization owners, above the policy maximum of {limit}"),
    "outside_collaborator_admin": ("high", False, "R6", "outside collaborator with {perm} permission"),
    "no_manager_or_unknown_title": ("low", False, "R7", "privileged holder whose roster row lacks a {missing}"),
    "unmatched_identity": ("medium", False, "R8",
                           "code-host member who could not be matched to the roster (no handle, mapping or email)"),
    "service_account_review": ("low", False, "R9", "looks like a service account: review its owner manually"),
}


@dataclass(frozen=True)
class Finding:
    person: str             # a label: roster name, else email, else login
    email: str              # "" when unknown
    system: str
    role: str
    kind: str
    severity: str
    evidence: str
    rule: str               # "R1 terminated_has_access: <rule text>"
    removal_candidate: bool
    weak_match: bool = False
    privileged: bool = False
    days_since: int | None = None   # days since last sign-in (None = never or unknown)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Finding:
        return Finding(**{k: data[k] for k in Finding.__dataclass_fields__ if k in data})

    def public(self) -> dict:
        """The catalog's Review shape."""
        return {k: v for k, v in self.to_dict().items() if k not in ("privileged", "days_since")}


def make(kind: str, *, person: str, email: str, system: str, role: str, evidence: str, weak: bool = False,
         privileged: bool = False, days_since: int | None = None, candidate: bool | None = None, **fill) -> Finding:
    severity, removable, rule_id, text = RULES[kind]
    is_candidate = (removable if candidate is None else candidate) and not weak
    return Finding(person, email, system, role, kind, severity, evidence,
                   f"{rule_id} {kind}: {text.format(**fill)}", is_candidate, weak, privileged, days_since)


def ordered(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Severity, then privileged first, then days since sign-in descending (unknown last), then name."""
    return tuple(sorted(findings, key=lambda f: (SEVERITY_ORDER.index(f.severity), not f.privileged,
                                                 f.days_since is None, -(f.days_since or 0), f.person.casefold(),
                                                 f.system.casefold())))


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out = {**out, f.kind: out.get(f.kind, 0) + 1}
    return out
