"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from code checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fact import FactSet
from request import Ask
from template import watch_out
from write import Story

GUESSED_SHARE = 0.2
MIN_POINTS = 3
FEW_POINTS = 6


@dataclass(frozen=True)
class Parsing:
    """How sure the table parse was: carried in state so follow-ups keep the same confidence inputs."""

    header_guessed: bool = False
    unparsed_share: float = 0.0

    def to_dict(self) -> dict:
        return {"header_guessed": self.header_guessed, "unparsed_share": self.unparsed_share}

    @staticmethod
    def from_dict(data: Mapping | None) -> Parsing:
        data = data or {}
        return Parsing(bool(data.get("header_guessed")), float(data.get("unparsed_share") or 0.0))


def confidence(facts: FactSet, story: Story, parsing: Parsing) -> str:
    counted = facts.shape != "findings"
    if parsing.header_guessed or parsing.unparsed_share > GUESSED_SHARE or story.template == "checks_failed":
        return "low"
    if counted and facts.points < MIN_POINTS:
        return "low"
    if facts.shape == "other" or story.repaired or story.dropped or story.template == "model_failed":
        return "medium"
    if counted and facts.points < FEW_POINTS:
        return "medium"
    return "high"


def build(facts: FactSet, story: Story, ask: Ask, parsing: Parsing, notes: Sequence[str] = ()) -> dict:
    level = confidence(facts, story, parsing)
    narrative = " ".join(x for x in (story.headline, story.body) if x)
    return {
        "summary": story.headline,
        "items": [f.to_dict() for f in facts.facts],
        "narrative": narrative,
        "headline": story.headline,
        "body": story.body,
        "shape": facts.shape,
        "facts": [{"id": f.id, "text": f.text} for f in facts.facts],
        "uses": list(story.uses),
        "watch_out": watch_out(facts),
        "audience": ask.audience,
        "length": ask.length,
        "caveats": [*facts.notes, *notes],
        "dropped": story.dropped,
        "repaired": story.repaired,
        "template_used": bool(story.template),
        "confidence": level,
        "needs_human": level == "low",
    }
