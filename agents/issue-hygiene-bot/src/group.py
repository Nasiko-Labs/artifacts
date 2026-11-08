"""Pure: findings grouped by owner (the assignee, from tool output only), owners ordered by flagged count
then name, and the unassigned list (never messaged). Also matching owners to checked chat users by
exact casefolded email."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from records import ChatUser, Finding, Owner


@dataclass(frozen=True)
class Grouped:
    owners: tuple[Owner, ...]
    unassigned: tuple[Finding, ...]


def owner_key(finding: Finding) -> str:
    issue = finding.issue
    if issue.assignee_email:
        return issue.assignee_email.casefold()
    return f"name:{issue.assignee_name.casefold()}" if issue.assignee_name else ""


def group(findings: Sequence[Finding]) -> Grouped:
    """`findings` already in display order (rules.finding_order); that order is kept per owner."""
    by_key: dict[str, list[Finding]] = {}
    unassigned: list[Finding] = []
    for finding in findings:
        key = owner_key(finding)
        if key:
            by_key.setdefault(key, []).append(finding)
        else:
            unassigned.append(finding)
    owners = [Owner(key=key, name=_name(items), email=items[0].issue.assignee_email, findings=tuple(items))
              for key, items in by_key.items()]
    owners.sort(key=lambda o: (-o.count, o.name.casefold(), o.key))
    return Grouped(tuple(owners), tuple(unassigned))


def _name(items: Sequence[Finding]) -> str:
    return next((f.issue.assignee_name for f in items if f.issue.assignee_name), "") or items[0].issue.assignee_email


def match_users(owners: Sequence[Owner], users: Sequence[ChatUser]) -> tuple[Owner, ...]:
    """Each owner with an email gets the chat user whose email is the same, casefolded. Nothing else matches:
    not a name, not a partial address."""
    by_email: Mapping[str, ChatUser] = {u.email.casefold(): u for u in users}
    matched = []
    for owner in owners:
        user = by_email.get(owner.email.casefold()) if owner.email else None
        matched.append(replace(owner, user_id=user.user_id) if user else owner)
    return tuple(matched)


def lookup_emails(owners: Sequence[Owner], limit: int) -> tuple[str, ...]:
    """Distinct owner emails to look up in chat, in owner order, at most `limit`."""
    return tuple(dict.fromkeys(o.email for o in owners if o.email))[:limit]
