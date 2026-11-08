"""Pure: what the review could and could not cover, and the confidence and needs-human flags, computed in
code from those facts (never from a model's say-so)."""

from __future__ import annotations

from dataclasses import dataclass

REJECTED_HIGH_LIMIT = 0.05
MATCHED_LOW_LIMIT = 0.5


@dataclass(frozen=True)
class Inputs:
    export_rows: int
    export_rejected: int
    roster_rows: int
    roster_rejected: int
    code_host_requested: bool
    code_host_reviewed: bool
    holders: int
    matched: int
    weak: int


def notes(i: Inputs, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    out = list(extra)
    if not i.roster_rows:
        out.append("No roster was pasted: terminated, not-on-roster and title checks were skipped; only dormancy, "
                   "owner count and outside-collaborator checks ran.")
    if not i.export_rows:
        out.append("No access export was pasted: sign-in and per-system checks were skipped.")
    if i.export_rejected:
        out.append(f"{i.export_rejected} export row(s) could not be read and were skipped.")
    if i.roster_rejected:
        out.append(f"{i.roster_rejected} roster row(s) could not be read and were skipped.")
    if i.weak:
        out.append(f"{i.weak} holder(s) matched to the roster by name only (weak) and were not drafted for removal.")
    return tuple(dict.fromkeys(out))


def confidence(i: Inputs) -> str:
    if i.holders and i.roster_rows and i.matched / i.holders < MATCHED_LOW_LIMIT:
        return "low"
    if not i.holders:
        return "low"
    rows = i.export_rows + i.roster_rows
    rejected = i.export_rejected + i.roster_rejected
    clean = rows and rejected / (rows + rejected) < REJECTED_HIGH_LIMIT
    code_host_ok = i.code_host_reviewed or not i.code_host_requested
    if i.roster_rows and i.export_rows and code_host_ok and clean and not i.weak:
        return "high"
    return "medium"


def needs_human(finding_count: int, conf: str) -> bool:
    """Access decisions are human: any finding needs one, and so does a low-confidence review."""
    return finding_count > 0 or conf == "low"
