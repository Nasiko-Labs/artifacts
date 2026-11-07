"""Pure: the verdict (with the prod guardrail), the confirm-intent follow-up and the suggested apply order.

The guardrail: in prod, a destroy or replace of a stateful resource is `block` until the user confirms that
address in a follow-up. Confirmation turns `block` into `review`, never `safe`, and never covers EX01."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import resources
from changes import Change, Finding

MAX_ADDRESSES_PER_STEP = 25
TIERS = ("foundation", "data", "compute", "edge")
GRAPH_NOTE = "Terraform orders by its own dependency graph; use this as a human checklist."
NOT_CONFIRMABLE = frozenset({"EX01"})
_CONFIRM = re.compile(r"\b(?:i )?(?:confirm(?:ed|ing)?|intended|intentional|on purpose|yes,? (?:destroy|delete|replace)"
                      r"(?:ing)?)\b", re.IGNORECASE)
_BARE_YES = re.compile(r"^\s*(?:yes|y|yep|yeah|correct)\s*[.!]?\s*$", re.IGNORECASE)
_ALL = re.compile(r"\b(?:all|every|both)\b", re.IGNORECASE)


def decide(findings: Sequence[Finding], has_changes: bool, has_delete: bool, integrity: bool) -> str:
    if not has_changes and not findings:
        return "safe"
    if any(f.blocking and not f.confirmed for f in findings):
        return "block"
    if has_delete or integrity or any(f.severity in ("high", "medium") or f.confirmed for f in findings):
        return "review"
    return "safe"


def blocking_addresses(findings: Sequence[Finding]) -> list[str]:
    return list(dict.fromkeys(f.address for f in findings if f.blocking and not f.confirmed))


@dataclass(frozen=True)
class Confirmation:
    addresses: tuple[str, ...] = ()     # confirmed this turn
    ask: tuple[str, ...] = ()           # blocking addresses to name when the request was ambiguous


def is_confirmation(text: str) -> bool:
    return bool(_CONFIRM.search(text or "") or _BARE_YES.match(text or ""))


def confirm(text: str, findings: Sequence[Finding]) -> Confirmation:
    """Which blocking addresses a follow-up confirms. A bare "yes" (or a confirm naming nothing) confirms only
    when exactly one address is blocking."""
    blocking = blocking_addresses(findings)
    named = [a for a in blocking if a in (text or "")]
    if named:
        return Confirmation(tuple(named))
    if blocking and _ALL.search(text or ""):
        return Confirmation(tuple(blocking))
    if len(blocking) == 1:
        return Confirmation(tuple(blocking))
    return Confirmation(ask=tuple(blocking))


def apply_confirmation(findings: Sequence[Finding], addresses: Sequence[str]) -> tuple[Finding, ...]:
    chosen = set(addresses)
    return tuple(replace(f, confirmed=True) if f.address in chosen and f.rule_id not in NOT_CONFIRMABLE else f
                 for f in findings)


def apply_order(changes: Sequence[Change]) -> list[dict]:
    """Tiers of the changes as a human checklist: foundation, data, compute, edge creates/updates; then replaces
    one at a time (stateful last); then destroys in reverse tier order."""
    groups: list[tuple[str, list[str], str]] = []
    building = [c for c in changes if c.action in ("create", "update", "import")]
    notes = {"foundation": "Networks, keys, IAM and secrets the rest depend on.",
             "data": "Data stores.", "compute": "Compute and platform.", "edge": "DNS, certificates, CDN, monitoring."}
    for tier in TIERS:
        addresses = [c.address for c in building if resources.tier(c.resource_type) == tier]
        groups.append((tier, addresses, notes[tier]))
    replaces = sorted((c for c in changes if c.action == "replace"), key=lambda c: resources.is_stateful(c.resource_type))
    groups.append(("replace", [c.address for c in replaces],
                   "One at a time; take a snapshot or backup first for the stateful ones (listed last)."))
    destroys = [c for c in changes if c.action == "delete"]
    ordered = [c.address for tier in reversed(TIERS) for c in destroys if resources.tier(c.resource_type) == tier]
    groups.append(("destroy", ordered, "Confirm nothing still references each one."))
    steps = []
    for tier, addresses, note in groups:
        if not addresses:
            continue
        extra = len(addresses) - MAX_ADDRESSES_PER_STEP
        shown = addresses[:MAX_ADDRESSES_PER_STEP]
        full_note = note + (f" (+{extra} more)" if extra > 0 else "")
        steps.append({"step": len(steps) + 1, "tier": tier, "addresses": shown, "note": full_note})
    return steps
