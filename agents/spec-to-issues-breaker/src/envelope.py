"""The output contract, computed in code: `confidence` and `needs_human` from the checks (never from the
model), the envelope for the data part, and the fenced-JSON text MAF reads when asked for JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from records import Project, ProposedIssue, Team, issues_to


@dataclass(frozen=True)
class Checks:
    sources_planned: int = 2
    sources_read: int = 2
    capped: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    unclear: bool = False
    dropped_lines: bool = False
    ambiguous: bool = False
    awaiting_approval: bool = False


def confidence(c: Checks) -> str:
    share = c.dropped / c.extracted if c.extracted else 0.0
    if c.sources_read == 0 or share > 0.2 or c.salvaged:
        return "low"
    gap = c.sources_read < c.sources_planned
    if gap or c.capped or c.dropped or c.unclear or c.dropped_lines:
        return "medium"
    return "high"


def needs_human(c: Checks, issues: Sequence[ProposedIssue]) -> bool:
    flagged = any(i.estimate is None or i.exists for i in issues)
    return confidence(c) == "low" or flagged or c.ambiguous or c.awaiting_approval


def build(summary: str, issues: Sequence[ProposedIssue], checks: Checks, team: Team | None,
          project: Project | None) -> dict:
    items = [{**d, "url": d["url"] or None} for d in issues_to(issues)]
    return {"summary": summary, "items": items, "confidence": confidence(checks),
            "needs_human": needs_human(checks, issues), "proposed_issues": items,
            "project": {"id": project.id, "name": project.name} if project else None,
            "team": {"id": team.id, "key": team.key, "name": team.name} if team else None,
            "created": [{"local_id": i.local_id, "identifier": i.identifier, "url": i.url or None}
                        for i in issues if i.status == "created"]}


def as_json_text(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
