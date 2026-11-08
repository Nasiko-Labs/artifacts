"""Pure: coverage lines and the output envelope (`summary`, `items`, `confidence`, `needs_human`, `drift[]`).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence

import audit
import drift as dr
from analyse import MAX_RESOURCES, Analysis


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def coverage_notes(a: Analysis, show_extras: bool, extra: Sequence[str] = ()) -> tuple[str, ...]:
    notes = list(extra)
    if a.unlocated:
        notes.append("Couldn't find the IaC block in the code host for: " + ", ".join(a.unlocated[:10]) + ".")
    if a.capped:
        notes.append(f"Only the first {MAX_RESOURCES} resources were looked up in the code host; not looked up: "
                     + ", ".join(a.capped[:10]) + ".")
    if a.unresolved:
        notes.append(f"{plural(a.unresolved, 'attribute')} use variables or computed values and were not compared.")
    if a.extras and not show_extras:
        notes.append(f"{plural(len(a.extras), 'attribute')} set live but not in code are hidden; say \"show extras\".")
    if not a.audit_given and a.drifts:
        notes.append("No audit events pasted, so who changed things by hand is unknown. Paste them after a line "
                     "`audit:` to see it.")
    elif a.audit_given and any(d.changed_by == audit.NO_MATCH for d in a.drifts):
        unmatched = dr.resources(d for d in a.drifts if d.changed_by == audit.NO_MATCH)
        notes.append("No pasted audit event matched: " + ", ".join(unmatched[:10]) + ".")
    return tuple(notes + list(a.notes))


def confidence(a: Analysis, show_extras: bool, fetch_gaps: bool) -> str:
    if a.iac_source != "code host":
        return "low"
    high_unmatched = a.audit_given and any(d.changed_by == audit.NO_MATCH for d in dr.high(a.drifts))
    if a.unresolved or a.unlocated or a.capped or fetch_gaps or high_unmatched or (a.extras and not show_extras):
        return "medium"
    return "high"


def needs_human(drifts: Sequence[dr.Drift]) -> bool:
    return bool(dr.high(drifts))


def summary(a: Analysis, drifts: Sequence[dr.Drift], repo: str) -> str:
    where = f" in {repo}" if repo else ""
    if not drifts:
        return (f"No drift{where}: {plural(a.compared, 'attribute')} compared, "
                f"{plural(a.unresolved, 'attribute')} skipped.")
    high = len(dr.high(drifts))
    return (f"{plural(len(drifts), 'drifted attribute')} in {plural(len(dr.resources(drifts)), 'resource')}{where} "
            f"({high} high); {plural(a.compared, 'attribute')} compared, {a.unresolved} skipped.")


def build(a: Analysis, drifts: Sequence[dr.Drift], *, repo: str, headline: str, notes: Sequence[str],
          show_extras: bool, fetch_gaps: bool) -> dict:
    items = [d.to_dict() for d in drifts]
    env = {
        "summary": summary(a, drifts, repo), "items": items, "drift": items,
        "confidence": confidence(a, show_extras, fetch_gaps), "needs_human": needs_human(drifts),
        "headline": headline, "repo": repo, "form": a.form, "compared": a.compared, "unresolved": a.unresolved,
        "iac_source": a.iac_source, "coverage": list(notes), "read_only": True,
    }
    return {**env, "extras": [d.to_dict() for d in a.extras]} if show_extras else env
