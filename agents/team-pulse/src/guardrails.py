"""Pure: the people-analytics guardrails enforced in code, not left to a prompt.

- `asks_for_judgment(message)`: a request to rank, compare, rate, score or judge people (or predict
  who leaves) is declined before any tool or LLM call.
- `has_judgment_words(text)`: every rendered string and every follow-up answer is checked for words
  that judge a person; a follow-up answer that trips it is replaced by a fixed note.
"""

from __future__ import annotations

import re

_JUDGMENT_REQUESTS = (
    r"\brank(?:s|ed|ing)?\b",
    r"\bleader ?board\b",
    r"\b(?:rate|rating|grade|score|scoring)\b(?! (?:limit|card))",
    r"\bcompare\b(?!.*\b(?:own|usual|baseline|before|last month|previous)\b)",
    r"\bwho(?:'s| is| are)? (?:the )?(?:best|worst|top|bottom|weakest|strongest|laziest|slowest|fastest|most|least)\b",
    r"\b(?:most|least|more|less) productive\b",
    r"\b(?:top|low|high|under|best|worst)[ -]?perform(?:er|ers|ing|ance)?\b",
    r"\bunderperform\w*",
    r"\bperformance (?:review|rating|score|evaluation)\b",
    r"\bslack(?:ing|er|ers)\b",
    r"\blazy\b|\blaziest\b",
    r"\bnot pulling (?:their|his|her) weight\b",
    r"\b(?:fire|firing|let go|lay off|layoff|pip)\b",
    r"\b(?:quit|quitting|attrition|flight risk|likely to leave|will leave|going to leave)\b",
    r"\bburn(?:ing|ed|t)? ?out\b|\bburnout\b",
    r"\bdisengaged\b",
    r"\bwho works (?:the )?(?:hardest|least|most)\b",
)
_JUDGMENT = re.compile("|".join(_JUDGMENT_REQUESTS), re.IGNORECASE)

BANNED_WORDS = (
    "lazy", "slow", "slower", "slowest", "underperform", "underperformer", "underperforming",
    "low performer", "high performer", "top performer", "slacking", "slacker", "burnout", "burned out",
    "burnt out", "disengaged", "unproductive", "productive", "best", "worst", "top", "bottom",
    "weak", "weakest", "poor", "lagging", "laggard", "struggling", "unreliable",
)
_BANNED = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")


def asks_for_judgment(message: str) -> bool:
    return bool(_JUDGMENT.search(message or ""))


def has_judgment_words(text: str) -> bool:
    """URLs are ignored: a repository or ticket path is not a statement about a person."""
    return bool(_BANNED.search(_URL.sub(" ", text or "")))
