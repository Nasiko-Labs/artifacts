"""Pure: the fallback narrative, built from the top facts' own code sentences when the model's wording
fails the checks twice (or the model is unreachable). Headline, then support, then one watch-out."""

from __future__ import annotations

from dataclasses import dataclass

from fact import FactSet
from request import Ask

SUPPORT = {"one": 0, "short": 1, "default": 2, "exec": 2}
WATCH_OUT = {"one": False, "short": False, "default": True, "exec": True}


@dataclass(frozen=True)
class Narrative:
    headline: str
    body: str
    uses: tuple[str, ...]

    @property
    def text(self) -> str:
        return " ".join(x for x in (self.headline, self.body) if x)


def watch_out(facts: FactSet) -> str:
    quality = facts.quality()
    return f"Watch out: {_lower_first(quality[0].text)}" if quality else ""


def narrate(facts: FactSet, ask: Ask) -> Narrative:
    story, quality = facts.story(), facts.quality()
    lead = story or quality
    if not lead:
        return Narrative("", "", ())
    headline = lead[0]
    support = [f for f in story if f.id != headline.id][:SUPPORT[ask.length]]
    parts = [f.text for f in support]
    uses = [headline.id, *(f.id for f in support)]
    if WATCH_OUT[ask.length] and quality and quality[0].id != headline.id:
        parts.append(watch_out(facts))
        uses.append(quality[0].id)
    return Narrative(headline.text, " ".join(parts), tuple(uses))


def _lower_first(text: str) -> str:
    """'The last period…' → 'the last period…', but labels in capitals ('West has…', 'Q4…') stay as written."""
    first = text.split(" ", 1)[0]
    return text[0].lower() + text[1:] if first in ("The", "There") else text
