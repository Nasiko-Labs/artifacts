"""Pure: the word lists that keep the debrief evidence-based. Heuristics, labelled as prompts to ask,
never as findings; extend the tuples, not the logic.

- PERSONAL: personal characteristics that must never be read as signal (age, gender, race, religion,
  disability, health, pregnancy, family or marital status, sexual orientation, nationality, accent,
  appearance). A claim that mentions one is left out of the analysis and shown only as a quality flag.
- VAGUE: proxy or vague language that is flagged with a neutral prompt, never interpreted.
- DECISION: hire/no-hire wording that the agent must never say in its own voice.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PERSONAL_TERMS: tuple[str, ...] = (
    r"(?:his|her|their) age", r"age \d+", r"aged \d+", r"years old", r"too old", r"older (?:candidate|person|guy|woman|man)", r"elderly",
    r"gender", r"male", r"female", r"woman", r"women", r"girl",
    r"race(?! conditions?)", r"racial", r"ethnic\w*", r"nationality", r"national origin", r"immigrant\w*",
    r"foreigner\w*", r"native speaker", r"religio\w*", r"church", r"mosque", r"synagogue",
    r"christian", r"muslim", r"jewish", r"hindu", r"prays?", r"praying",
    r"disab\w*", r"wheelchair", r"deaf", r"blind(?! spots?)", r"autis\w*", r"adhd",
    r"health (?:issues?|problems?|conditions?)", r"illness", r"sick leave", r"medical (?:leave|conditions?|issues?)", r"mental health",
    r"pregnan\w*", r"maternity", r"paternity", r"kids", r"(?:has|have|his|her|their) children", r"family plans", r"has a family",
    r"married", r"marriage", r"wife", r"husband", r"spouse", r"single (?:mom|mother|dad|father|parent)",
    r"gay", r"lesbian", r"sexual orientation", r"transgender",
    r"accent", r"appearance", r"attractive", r"overweight", r"dressed", r"outfit", r"hijab", r"turban",
)
VAGUE_TERMS: tuple[str, ...] = (
    r"culture fit", r"cultural fit", r"not a (?:good )?fit", r"gut (?:feel|feeling)", r"vibes?", r"didn'?t click",
    r"too junior", r"too senior", r"aggressive", r"abrasive", r"bubbly", r"articulate", r"polished",
    r"(?:high|low|good|great|bad) energy", r"young", r"overqualified", r"likeable", r"likable", r"one of us",
)
DECISION_TERMS: tuple[str, ...] = (
    r"recommend hiring", r"should hire", r"no hire", r"no-hire", r"reject\w*", r"offer\w*", r"pass on",
    r"strong candidate", r"weak candidate", r"not a fit", r"good fit", r"hire (?:him|her|them)",
    r"we should (?:not )?hire",
)

_PERSONAL = re.compile(rf"\b(?:{'|'.join(PERSONAL_TERMS)})\b", re.IGNORECASE)
_VAGUE = re.compile(rf"\b(?:{'|'.join(VAGUE_TERMS)})\b", re.IGNORECASE)
_DECISION = re.compile(rf"\b(?:{'|'.join(DECISION_TERMS)})\b", re.IGNORECASE)
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def personal_term(text: str) -> str | None:
    """The first personal-characteristic term in the text, or None."""
    found = _PERSONAL.search(text or "")
    return found.group(0) if found else None


def vague_term(text: str) -> str | None:
    found = _VAGUE.search(text or "")
    return found.group(0) if found else None


def has_decision_words(text: str) -> bool:
    """Decision wording in the agent's own voice: text inside double quotes is a quote and exempt."""
    return bool(_DECISION.search(_QUOTED.sub(" ", text or "")))


def sentences(text: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in _SENTENCE.split(text or "") if s.strip())


def sentence_with(text: str, finder) -> Iterable[tuple[str, str]]:
    """(sentence, term) for every sentence of the text where `finder` finds a term."""
    for sentence in sentences(text):
        term = finder(sentence)
        if term:
            yield sentence, term
