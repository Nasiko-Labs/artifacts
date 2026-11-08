"""Pure: `confidence` and `needs_human`, computed in code from what the checks saw (never from the model),
and the envelope every digest reply carries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_DROP_SHARE_LOW = 0.2


@dataclass(frozen=True)
class Checks:
    source_read: bool                 # the desk (or pasted tickets) was read
    previous_read: bool = True
    sla_from_desk: bool = False
    capped: bool = False              # the 300 cap or a sampled count
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    themes_partial: bool = False
    ambiguous: bool = False
    open_breaches: int = 0


def confidence(c: Checks) -> str:
    share = c.dropped / c.extracted if c.extracted else 0.0
    if not c.source_read or share > _DROP_SHARE_LOW or c.salvaged:
        return "low"
    gaps = (not c.previous_read, not c.sla_from_desk, c.capped, c.dropped > 0, c.themes_partial)
    return "medium" if any(gaps) else "high"


def needs_human(c: Checks) -> bool:
    return confidence(c) == "low" or c.ambiguous or c.open_breaches > 0


def build(summary: str, digest: dict, markdown: str, breaches: Sequence[dict], c: Checks,
          notes: Sequence[str]) -> dict:
    return {"summary": summary, "items": list(digest["themes"]), "confidence": confidence(c),
            "needs_human": needs_human(c), "digest": digest, "digest_markdown": markdown,
            "breaches": list(breaches), "notes": list(notes)}
