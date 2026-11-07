"""Pure: the code lexicon pass. Always runs; adds the worst signals with the matched sentence as a
verbatim quote, even when the model missed them, and is the only analysis for lexicon-only tickets."""

from __future__ import annotations

import re

from model import Signal, Ticket

QUOTE_MAX = 160
_PATTERNS = (
    ("service_down", re.compile(r"(?<![-\w])down\b(?!-?load)|\boutage\b|\bunavailable\b|\b500 error\b|"
                                r"\b50[23]\b", re.IGNORECASE)),
    ("data_loss", re.compile(r"\blost (?:\w+ )?data\b|\bdata (?:was |is |got )?lost\b|\bdeleted\b|\bdisappeared\b|"
                             r"\bcorrupt", re.IGNORECASE)),
    ("security_concern", re.compile(r"\bbreach|\bvulnerab|\bleak|\bexposed\b|\bunauthori[sz]ed\b|\bphish",
                                    re.IGNORECASE)),
    ("churn_threat", re.compile(r"\b(?:will|going to|we'?ll|want to|plan to|about to|ready to) cancel\b|"
                                r"\bcancel (?:our|my|the) (?:account|subscription|plan|contract)\b|"
                                r"\bnot (?:going to )?renew|\bswitch(?:ing)? to\b", re.IGNORECASE)),
    ("legal_threat", re.compile(r"\blawyers?\b|\blegal action\b|\blegal\b|\bchargeback|\bregulator|"
                                r"\bgdpr complaint|\battorney|\blawsuit|\bsue\b", re.IGNORECASE)),
)
_SENTENCE_END = re.compile(r"[.!?\n]")


def scan(ticket: Ticket) -> tuple[Signal, ...]:
    text = ticket.text
    found = []
    for name, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            found.append(Signal(name, sentence_around(text, match.start(), match.end()), "lexicon"))
    return tuple(found)


def merge(ticket: Ticket, signals: tuple[Signal, ...]) -> tuple[Signal, ...]:
    """The model's signals, plus each lexicon signal the model did not report."""
    have = {s.name for s in signals}
    return (*signals, *(s for s in scan(ticket) if s.name not in have))


def sentence_around(text: str, start: int, end: int) -> str:
    """The sentence holding text[start:end], cut to QUOTE_MAX around the match; a verbatim substring."""
    left = max((m.end() for m in _SENTENCE_END.finditer(text, 0, start)), default=0)
    right_match = _SENTENCE_END.search(text, end)
    right = right_match.start() if right_match else len(text)
    left = max(left, end - QUOTE_MAX)
    right = min(right, left + QUOTE_MAX)
    return text[left:right].strip()
