"""The per-conversation deck as checkpointed (JSON-safe) and the turn's built file (never checkpointed)."""

from __future__ import annotations

from dataclasses import dataclass, field

import build_pptx
from dataparse import Table
from models import Deck, Theme, parse_deck
from structure import Report
from verify_pptx import sha256

MAX_PARTS_CHARS = 60_000


@dataclass(frozen=True)
class Stored:
    deck: Deck
    tables: dict[str, Table]
    report: Report
    file_name: str
    folder: str | None = None
    chart_failures: int = 0

    def build(self) -> tuple[bytes, str]:
        data = build_pptx.build(self.deck, self.tables)
        return data, sha256(data)


@dataclass
class Built:
    """What one turn produced, for the reply parts."""

    stored: Stored
    data: bytes
    sha: str
    envelope: dict = field(default_factory=dict)


def report_to_dict(report: Report) -> dict:
    return {"placeholders": report.placeholders, "dropped": report.dropped, "split": report.split,
            "notes": report.notes, "repaired": report.repaired}


def report_from_dict(data: dict | None) -> Report:
    data = data or {}
    return Report(placeholders=list(data.get("placeholders") or []), dropped=int(data.get("dropped") or 0),
                  split=int(data.get("split") or 0), notes=list(data.get("notes") or []),
                  repaired=bool(data.get("repaired")))


def tables_from(state: dict) -> dict[str, Table]:
    return {t["name"]: Table.from_dict(t) for t in state.get("tables") or []}


def stored_from(state: dict) -> Stored | None:
    deck = parse_deck(state.get("deck"))
    if deck is None:
        return None
    return Stored(deck=deck, tables=tables_from(state), report=report_from_dict(state.get("report")),
                  file_name=str(state.get("file_name") or ""), folder=state.get("folder"),
                  chart_failures=int(state.get("chart_failures") or 0))


def theme_of(state: dict) -> Theme:
    raw = state.get("theme")
    return Theme.model_validate(raw) if isinstance(raw, dict) else Theme()


def capped_parts(parts: list[str]) -> list[str]:
    """Grounding text kept across turns, newest kept when over the cap."""
    kept: list[str] = []
    total = 0
    for part in reversed(parts):
        if total + len(part) > MAX_PARTS_CHARS:
            break
        kept.insert(0, part)
        total += len(part)
    return kept
