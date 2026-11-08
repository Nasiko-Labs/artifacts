"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `affected[]`, `changes[]`).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model.

    high   the plan's counts match, every search ran with no failure and no cap, CODEOWNERS covered every
           repository with dependents, and every owner is known
    medium the cap was hit, some owners are unknown, a partial parse, a tool failure, or tag-only changes
    low    the code host was not searched (not connected, or no repository given)
"""

from __future__ import annotations

from analysis import Analysis

HIGH_LEVELS = ("critical", "high")


def confidence(a: Analysis) -> str:
    if not a.searched:
        clean = not a.mismatch and not any(c.tag_only for c in a.changes)
        return "high" if clean else "medium"
    if not a.code_host:
        return "low"
    capped = a.eligible > len(a.searched)
    unknown_owner = any(r.owner == "unknown" for r in a.affected)
    if a.mismatch or capped or unknown_owner or not a.complete or not a.codeowners:
        return "medium"
    return "high"


def needs_human(a: Analysis) -> bool:
    return any(c.risk_level in HIGH_LEVELS for c in a.changes) or any(r.environment == "prod" for r in a.affected)


def summary(a: Analysis) -> str:
    levels = [c.risk_level for c in a.changes]
    prod = sum(1 for r in a.affected if r.environment == "prod")
    head = (f"{len(a.changes)} change(s): {levels.count('critical')} critical, {levels.count('high')} high, "
            f"{levels.count('medium')} medium, {levels.count('low')} low")
    if not a.searched:
        return head + "; no dependents to search."
    if not a.code_host:
        return head + "; dependents not searched."
    return head + f"; {len(a.affected)} dependent(s) found, {prod} in prod."


def build(a: Analysis) -> dict:
    rows = [r.to_dict() for r in a.affected]
    return {
        "summary": summary(a), "items": rows, "affected": rows,
        "changes": [c.to_dict() for c in a.changes],
        "confidence": confidence(a), "needs_human": needs_human(a),
        "repo": a.repo, "also_searched": list(a.extra_repos), "plan_environment": a.env,
        "plan": {"format": a.format, "stated": a.stated, "parsed": a.parsed, "counts_match": not a.mismatch},
        "searched": list(a.searched), "eligible_for_search": a.eligible,
        "check_before_apply": list(a.checks), "coverage": list(a.notes),
        "narrative": a.narrative, "what_could_break": [{"address": x, "text": t} for x, t in a.breaks],
        "read_only": True,
    }
