"""Pure: follow-ups on the stored review, parsed in code. All of them re-render; none calls the model.

    "only blockers" / "only metrics" / "vague requirements" / "missing sections" / "list the questions"
    "show score breakdown" / "show all" / "as json" / "what changed" (no versions kept)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RULES = (
    ("json", re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the (?:result|review))? )?"
                        r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)),
    ("versions", re.compile(r"\bwhat(?:'s| has| have)? changed\b|\bdiff\b|\bcompare (?:it |the )?(?:to|with)\b|"
                            r"\bprevious (?:version|draft)\b", re.IGNORECASE)),
    ("breakdown", re.compile(r"\bbreakdown\b|\bdeductions?\b|\bhow (?:did you|was it|is it) score|\bwhy (?:this|the|"
                             r"that) score\b|\bexplain the score\b", re.IGNORECASE)),
    ("blockers", re.compile(r"\bblockers?\b|\bblocking\b", re.IGNORECASE)),
    ("metrics", re.compile(r"\bmetrics?\b|\bmeasurable\b|\bkpis?\b", re.IGNORECASE)),
    ("sections", re.compile(r"\bmissing sections?\b|\bwhich sections?\b|\bsections? (?:are )?missing\b",
                            re.IGNORECASE)),
    ("requirements", re.compile(r"\brequirements?\b", re.IGNORECASE)),
    ("questions", re.compile(r"\bquestions?\b", re.IGNORECASE)),
    ("all", re.compile(r"^\W*(?:show )?(?:all|everything|the full (?:list|review|result)|full (?:list|review))\W*$",
                       re.IGNORECASE)),
)
MAX_FOLLOWUP_WORDS = 25


@dataclass(frozen=True)
class Command:
    kind: str   # json | versions | breakdown | blockers | metrics | sections | requirements | questions | all


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    if not message or len(message.split()) > MAX_FOLLOWUP_WORDS:
        return None
    return next((Command(kind) for kind, rx in _RULES if rx.search(message)), None)
