"""Pure: words that judge a person rather than describe what happened, and questions that ask for a verdict.
Any text carrying one is rewritten to its quote or dropped; it never reaches the output."""

from __future__ import annotations

import re

_JUDGEMENT = re.compile(
    r"\b(?:lazy|laziness|struggl\w*|underperform\w*|under-perform\w*|unmotivated|demotivated|difficult|"
    r"incompetent|weak performer|low performer|poor performer|disengaged|unengaged|less engaged|"
    r"bad attitude|attitude problem|unreliable|slacking|slacker|burn(?:ed|t)? out|burnout|toxic|"
    r"not a team player|frustrated|unhappy|upset|stressed|anxious|overwhelmed)\b",
    re.IGNORECASE,
)
_VERDICT_REQUEST = re.compile(
    r"\b(?:is|was|are)\s+(?:she|he|they|\w+)\s+(?:a\s+)?(?:good|bad|great|weak|strong|top|low|high|poor)\s+"
    r"(?:performer|engineer|employee|fit)\b|\bunderperform\w*|\bperformance (?:review|rating|verdict)\b|"
    r"\b(?:should i|do i) (?:fire|let go|promote|demote|pip)\b|\brate (?:her|him|them)\b|\bwho is better\b|"
    r"\bcompare (?:her|him|them)\b|\bhow (?:is|was) (?:she|he|they) (?:doing|performing)\b",
    re.IGNORECASE,
)


def has_judgement(text: str) -> bool:
    return bool(_JUDGEMENT.search(text or ""))


def asks_for_verdict(text: str) -> bool:
    """A request to evaluate, rate or compare the person: declined in code, no call."""
    return bool(_VERDICT_REQUEST.search(text or ""))
