"""Pure: the verdict, the readiness count, `confidence` and `needs_human`, all in code. The model's opinion of
readiness is never read."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from config import DROPPED_SHARE_LOW
from records import Row

GO, CONDITIONAL, NO_GO = "go", "conditional", "no-go"
AREAS = ("engineering", "docs", "support", "marketing")


@dataclass(frozen=True)
class RunMeta:
    """How the evidence was gathered this turn."""

    read: tuple[str, ...] = ()              # areas read (tool or pasted)
    unread: tuple[str, ...] = ()            # areas not read: missing / unavailable
    capped: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    ambiguous_input: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def blockers(rows: Sequence[Row]) -> list[Row]:
    return [r for r in rows if r.blocking and r.status == "fail"]


def verdict(rows: Sequence[Row], unread: Sequence[str]) -> str:
    if blockers(rows):
        return NO_GO
    if unread or any(r.status in ("warn", "unknown") for r in rows):
        return CONDITIONAL
    return GO


def passed(rows: Sequence[Row]) -> tuple[int, int]:
    return sum(r.status == "pass" for r in rows), len(rows)


def dropped_share(meta: RunMeta) -> float:
    return meta.dropped / meta.extracted if meta.extracted else 0.0


def confidence(meta: RunMeta) -> str:
    if not meta.read or meta.salvaged or dropped_share(meta) > DROPPED_SHARE_LOW:
        return "low"
    if meta.unread or meta.capped or meta.dropped:
        return "medium"
    return "high"


def needs_human(rows: Sequence[Row], meta: RunMeta, level: str) -> bool:
    unknown_rows = any(r.status == "unknown" for r in rows)
    return (level == "low" or meta.ambiguous_input
            or (verdict(rows, meta.unread) == CONDITIONAL and (unknown_rows or bool(meta.unread))))
