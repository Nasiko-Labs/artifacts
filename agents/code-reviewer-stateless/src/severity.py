"""Pure: the severity a comment may carry. The model proposes one; code caps it.

| severity | meaning                                                         | author action      |
| critical | security hole, data loss, broken functionality, crash          | blocks merge       |
| required | a real defect or a clear violation of a stated standard         | fix before merge   |
| optional | worthwhile suggestion (structure, performance, smell)           | consider           |
| nit      | minor, skippable                                                | ignore freely      |
"""

from __future__ import annotations

import re

SEVERITIES = ("critical", "required", "optional", "nit")
RANK = {s: n for n, s in enumerate(SEVERITIES)}
CRITICAL_CATEGORIES = frozenset({"security", "data-loss", "crash", "broken-behaviour"})
SOFT_CATEGORIES = frozenset({"style", "readability", "naming", "documentation", "docs", "smell", "maintainability",
                             "simplicity", "nit", "formatting", "dead-code"})
SMELLS = ("mysterious name", "duplicated code", "feature envy", "data clumps", "primitive obsession",
          "repeated switches", "shotgun surgery", "divergent change", "speculative generality", "message chains",
          "middle man", "refused bequest")
_SMELL = re.compile(r"\bpossible (?:" + "|".join(SMELLS) + r")\b", re.IGNORECASE)
_SYNONYMS = {"blocker": "critical", "blocking": "critical", "high": "required", "major": "required",
             "must": "required", "medium": "optional", "minor": "optional", "consider": "optional",
             "suggestion": "optional", "low": "nit", "fyi": "nit", "trivial": "nit"}
_CATEGORY_SYNONYMS = {"broken-behavior": "broken-behaviour", "broken behaviour": "broken-behaviour",
                      "broken behavior": "broken-behaviour", "bug": "broken-behaviour", "data loss": "data-loss",
                      "dataloss": "data-loss"}


def normal_severity(value: object) -> str:
    text = str(value or "").strip().casefold()
    text = _SYNONYMS.get(text, text)
    return text if text in RANK else "optional"


def normal_category(value: object) -> str:
    text = str(value or "").strip().casefold().replace("_", "-")
    return _CATEGORY_SYNONYMS.get(text, text) or "general"


def is_smell(category: str, comment: str) -> bool:
    return category == "smell" or category in SMELLS or bool(_SMELL.search(comment or ""))


def lower(a: str, b: str) -> str:
    """The less severe of two severities."""
    return a if RANK[a] >= RANK[b] else b


def capped(severity: str, category: str, axis: str, comment: str, *, pre_existing: bool,
           rule_verified: bool) -> str:
    """Code's cap on the model's severity."""
    result = severity
    if result == "critical" and category not in CRITICAL_CATEGORIES:
        result = "required"
    if category in SOFT_CATEGORIES or is_smell(category, comment):
        result = lower(result, "optional")
    if axis == "standards":
        result = lower(result, "required" if rule_verified else "optional")
    if pre_existing:
        result = lower(result, "optional")
    return result
