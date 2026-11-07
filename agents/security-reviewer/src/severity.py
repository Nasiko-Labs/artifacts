"""Pure: severity is a fact from the rule table. The model's triage may lower it by one level, never raise
it; free-form findings get theirs from their category."""

from __future__ import annotations

LEVELS = ("critical", "high", "medium", "low", "info")
CATEGORY_SEVERITY = {
    "authz-missing": "high", "idor": "high", "ssrf": "high", "injection": "high", "auth-bypass": "high",
    "path-traversal": "high", "mass-assignment": "medium", "race": "medium", "business-logic": "medium",
    "info-leak": "low",
}
CATEGORY_CWE = {
    "authz-missing": "CWE-862", "idor": "CWE-639", "ssrf": "CWE-918", "injection": "CWE-74", "auth-bypass": "CWE-287",
    "path-traversal": "CWE-22", "mass-assignment": "CWE-915", "race": "CWE-362", "business-logic": "CWE-840",
    "info-leak": "CWE-200",
}
CATEGORIES = tuple(CATEGORY_SEVERITY)


def rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else len(LEVELS)


def lower(level: str) -> str:
    """One level down; info stays info."""
    return LEVELS[min(rank(level) + 1, len(LEVELS) - 1)]


def at_or_above(level: str, floor: str) -> bool:
    return rank(level) <= rank(floor)


def counts(findings: list[dict]) -> dict[str, int]:
    out = {level: 0 for level in LEVELS}
    for f in findings:
        out[f["severity"]] = out.get(f["severity"], 0) + 1
    return out


def ordered(findings: list[dict]) -> list[dict]:
    """Critical first; stable within a level (input order)."""
    return sorted(findings, key=lambda f: rank(f["severity"]))
