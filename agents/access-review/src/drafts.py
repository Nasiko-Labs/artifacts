"""Pure: removal requests, drafted by a code template from removal-candidate findings only, grouped by
system for batching. No model text is ever part of a request, and nothing is sent: the user copies it."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from findings import Finding

TEMPLATE = ("Request: remove {person} ({email}) from {system} role {role}. Reason: {reason}. Evidence: {evidence}. "
            "Requested by: [your name]. Please confirm completion.")


@dataclass(frozen=True)
class Removal:
    person: str
    system: str
    role: str
    reason: str
    evidence: str
    request_text: str
    rule: str               # "R1 terminated_has_access": the rule id and finding kind

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Removal:
        return Removal(**{k: data[k] for k in Removal.__dataclass_fields__ if k in data})

    def public(self) -> dict:
        """The catalog's Removal shape (the rule id stays inside `reason`)."""
        return {k: v for k, v in self.to_dict().items() if k != "rule"}


def draft(findings: Iterable[Finding]) -> tuple[Removal, ...]:
    """One request per removal-candidate finding (person + system), grouped by system."""
    out: dict[tuple[str, str, str], Removal] = {}
    for f in findings:
        if not f.removal_candidate or f.weak_match:
            continue
        key = (f.system.casefold(), (f.email or f.person).casefold(), f.role.casefold())
        if key in out:
            continue
        reason = f.rule
        text = TEMPLATE.format(person=f.person, email=f.email or "email unknown", system=f.system, role=f.role,
                               reason=reason, evidence=f.evidence)
        out = {**out, key: Removal(f.person, f.system, f.role, reason, f.evidence, text, f.rule.split(":", 1)[0])}
    return tuple(sorted(out.values(), key=lambda r: (r.system.casefold(), r.person.casefold(), r.role.casefold())))


def by_system(removals: Iterable[Removal]) -> dict[str, tuple[Removal, ...]]:
    grouped: dict[str, tuple[Removal, ...]] = {}
    for r in removals:
        grouped = {**grouped, r.system: (*grouped.get(r.system, ()), r)}
    return grouped
