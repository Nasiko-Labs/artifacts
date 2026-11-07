"""Pure: options read from the user's words: framework, target callables and the import path."""

from __future__ import annotations

import re

DEFAULT_FRAMEWORK = {"python": "pytest", "javascript": "jest", "typescript": "jest", "go": "go"}
FRAMEWORKS_FOR = {"python": ("pytest", "unittest"), "javascript": ("jest", "vitest"),
                  "typescript": ("jest", "vitest"), "go": ("go",)}
_FRAMEWORK_WORDS = (("unittest", re.compile(r"\bunittest\b|\bunit ?test\.TestCase\b", re.IGNORECASE)),
                    ("pytest", re.compile(r"\bpytest\b", re.IGNORECASE)),
                    ("vitest", re.compile(r"\bvitest\b", re.IGNORECASE)),
                    ("jest", re.compile(r"\bjest\b", re.IGNORECASE)),
                    ("go", re.compile(r"\btable[- ]driven\b|\bgo test\b|\btesting package\b", re.IGNORECASE)))
_BACKTICK = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`")
_NAMED = re.compile(r"\b(?:tests? for|test|for|only|also|add tests for)\s+(?:the\s+)?([A-Za-z_][\w.]*)(?:\(\))?",
                    re.IGNORECASE)
_MODULE = re.compile(r"\bmodule(?: path)?(?: is|:)?\s+`?([A-Za-z_./][\w./-]*)`?|\bimport(?:ed)? (?:it )?from\s+"
                     r"`?([A-Za-z_./][\w./-]*)`?", re.IGNORECASE)
_STOP = {"this", "that", "these", "the", "it", "my", "a", "an", "function", "functions", "class", "code", "module",
         "me", "them", "edge", "pytest", "jest", "vitest", "unittest", "go", "python", "typescript", "javascript",
         "all", "each", "every", "both", "tests", "test", "unit", "more", "negative", "with", "and", "too"}


def framework_in(text: str) -> str:
    """A framework the user named, or ""."""
    return next((name for name, rx in _FRAMEWORK_WORDS if rx.search(text or "")), "")


def framework_for(lang: str, text: str, style: str = "") -> tuple[str, str]:
    """(framework, note). The user's choice when it fits the language, else the language default."""
    named = framework_in(text) or framework_in(style)
    if named and named in FRAMEWORKS_FOR.get(lang, ()):
        return named, ""
    default = DEFAULT_FRAMEWORK[lang]
    note = f"{named} doesn't fit {lang} code, so I used {default}." if named else ""
    return default, note


def targets_in(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(explicit backticked names, loosely named words). Loose words count only if they match the code."""
    explicit = tuple(dict.fromkeys(_BACKTICK.findall(text or "")))
    loose = tuple(dict.fromkeys(w for w in _NAMED.findall(text or "") if w.casefold() not in _STOP))
    return explicit, loose


def module_in(text: str) -> str:
    match = _MODULE.search(text or "")
    return (match.group(1) or match.group(2)).rstrip(".") if match else ""


def existing_names(style: str) -> frozenset[str]:
    names = re.findall(r"^\s*(?:async\s+)?def\s+(test_\w+)|^\s*func\s+(Test\w+)", style or "", re.MULTILINE)
    js = re.findall(r"\b(?:it|test)\(\s*[\"'`](.+?)[\"'`]", style or "")
    return frozenset([a or b for a, b in names] + js)
