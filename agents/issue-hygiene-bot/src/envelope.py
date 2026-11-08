"""Pure: the result of one hygiene check as stored between turns, the structured envelope every agent returns
(`summary`, `items`, `confidence`, `needs_human` + this agent's fields), and `confidence` / `needs_human`
computed in code from the checks, never taken from the model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

import rules
from records import Finding, Owner, finding_from, owner_from

SENT_LIKE = {"sent": "sent", "skipped": "skipped"}


@dataclass(frozen=True)
class Quality:
    """What the checks saw this turn; `confidence` follows from it."""

    tracker_ok: bool = True
    chat_ok: bool = True
    capped: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False

    @property
    def confidence(self) -> str:
        if not self.tracker_ok or self.salvaged or (self.extracted and self.dropped * 5 > self.extracted):
            return "low"
        if not self.chat_ok or self.capped or self.dropped:
            return "medium"
        return "high"


@dataclass(frozen=True)
class Result:
    label: str                       # "Platform", or "all teams read"
    owners: tuple[Owner, ...]
    unassigned: tuple[Finding, ...]
    scores: tuple[rules.TeamScore, ...]
    notes: tuple[str, ...]
    quality: Quality
    chat_ok: bool

    @property
    def flagged(self) -> int:
        return sum(o.count for o in self.owners) + len(self.unassigned)

    @property
    def open_total(self) -> int:
        return sum(s.open_total for s in self.scores)

    def to_state(self) -> dict:
        return {"label": self.label, "owners": [asdict(o) for o in self.owners],
                "unassigned": [asdict(f) for f in self.unassigned], "scores": [asdict(s) for s in self.scores],
                "notes": list(self.notes), "quality": asdict(self.quality), "chat_ok": self.chat_ok}

    @staticmethod
    def from_state(data: Mapping[str, Any]) -> Result:
        return Result(label=str(data.get("label") or ""), owners=tuple(owner_from(o) for o in data.get("owners") or ()),
                      unassigned=tuple(finding_from(f) for f in data.get("unassigned") or ()),
                      scores=tuple(rules.TeamScore(**{**s, "open_total": int(s["open_total"]), "flagged": int(s["flagged"])})
                                   for s in data.get("scores") or ()),
                      notes=tuple(data.get("notes") or ()), quality=Quality(**_quality(data.get("quality") or {})),
                      chat_ok=bool(data.get("chat_ok")))


def _quality(raw: Mapping[str, Any]) -> dict:
    return {k: (int(v) if k in ("extracted", "dropped") else bool(v)) for k, v in raw.items()}


def dm_status(owner: Owner, outcomes: Mapping[str, str]) -> str:
    outcome = outcomes.get(owner.key)
    if outcome in SENT_LIKE:
        return SENT_LIKE[outcome]
    if outcome:
        return "drafted"
    return "not_drafted" if owner.user_id else "no_chat_user"


def item(finding: Finding, owner: Owner | None, status: str) -> dict:
    issue = finding.issue
    return {"issue_id": issue.id, "title": issue.title, "url": issue.url or None,
            "owner": owner.name if owner else None, "owner_email": (owner.email or None) if owner else None,
            "rules": list(finding.rules), "age_days": finding.age_days, "days_since_update": finding.days_since_update,
            "suggested_actions": list(finding.actions), "dm_status": status}


def build(result: Result, outcomes: Mapping[str, str], summary: str) -> dict:
    items = [item(f, o, dm_status(o, outcomes)) for o in result.owners for f in o.findings]
    unsent = any(dm_status(o, outcomes) not in ("sent", "skipped") for o in result.owners)
    confidence = result.quality.confidence
    return {"summary": summary, "items": items, "confidence": confidence,
            "needs_human": confidence == "low" or unsent, "hygiene_items": items,
            "unassigned": [item(f, None, "not_drafted") for f in result.unassigned],
            "team_scores": [{"team": s.team, "open": s.open_total, "flagged": s.flagged, "percent": s.percent,
                             "uses_estimates": s.uses_estimates} for s in result.scores],
            "notes": list(result.notes)}


def as_json(data: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```"
