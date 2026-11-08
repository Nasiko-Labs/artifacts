"""Pure: the finished retro as one record (kept in the checkpointer and on a post pause), and its
`confidence` / `needs_human`, computed from the checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from metrics import Metrics
from records import NO_REASON, Cycle, Issue, Spill, Team, from_state


@dataclass(frozen=True)
class Retro:
    team: Team
    cycle: Cycle
    metrics: Metrics
    shipped: tuple[Issue, ...] = ()
    added: tuple[Issue, ...] = ()
    spills: tuple[Spill, ...] = ()
    prompts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    confidence: str = "high"
    needs_human: bool = False

    def to_state(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_state(raw: Mapping[str, Any]) -> Retro:
        return Retro(team=from_state(Team, raw["team"]), cycle=from_state(Cycle, raw["cycle"]),
                     metrics=from_state(Metrics, raw["metrics"]), shipped=from_state(Issue, raw.get("shipped") or []),
                     added=from_state(Issue, raw.get("added") or []), spills=from_state(Spill, raw.get("spills") or []),
                     prompts=tuple(str(p) for p in raw.get("prompts") or ()),
                     notes=tuple(str(n) for n in raw.get("notes") or ()),
                     confidence=str(raw.get("confidence") or "low"), needs_human=bool(raw.get("needs_human")))


@dataclass(frozen=True)
class Quality:
    """What the checks saw this turn."""

    tracker_read: bool = True
    chat_searched: bool = True
    gaps: bool = False            # a coverage line was shown
    capped: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    created_fallback: int = 0


def confidence(q: Quality) -> str:
    share = q.dropped / q.extracted if q.extracted else 0.0
    if not q.tracker_read or share > 0.2 or q.salvaged:
        return "low"
    if q.gaps or q.capped or q.dropped or not q.chat_searched or q.created_fallback:
        return "medium"
    return "high"


def needs_human(level: str, spills: tuple[Spill, ...]) -> bool:
    unexplained = sum(1 for s in spills if s.reason == NO_REASON)
    return level == "low" or (bool(spills) and unexplained * 2 > len(spills))
