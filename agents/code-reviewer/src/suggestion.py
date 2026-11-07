"""Pure: a suggested replacement is shown only when it is non-empty, differs from the original, keeps
brackets and quotes balanced when the original was balanced, has at most MAX_SUGGESTION_LINES lines, and (for
Python and JSON files) still parses when the original did. A failing suggestion is dropped; the comment stays."""

from __future__ import annotations

import ast
import json
import textwrap
import unicodedata

MAX_SUGGESTION_LINES = 15
_PAIRS = {")": "(", "]": "[", "}": "{"}


def _same(a: str, b: str) -> bool:
    def flat(text: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", text).split())
    return flat(a) == flat(b)


def balanced(text: str) -> bool:
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in text:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            stack.append(ch)
        elif ch in _PAIRS and (not stack or stack.pop() != _PAIRS[ch]):
            return False
    return not stack and quote is None


def _parses(text: str, path: str) -> bool | None:
    """True/False for a language code can check cheaply; None when it cannot tell."""
    lower = path.casefold()
    if lower.endswith(".py"):
        try:
            ast.parse(textwrap.dedent(text))
        except (SyntaxError, ValueError):
            return False
        return True
    if lower.endswith(".json"):
        try:
            json.loads(text)
        except ValueError:
            return False
        return True
    return None


def checked(value: object, original: str, path: str) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip("\n")
    if not text.strip() or _same(text, original) or len(text.split("\n")) > MAX_SUGGESTION_LINES:
        return None
    if balanced(original) and not balanced(text):
        return None
    if _parses(original, path) and _parses(text, path) is False:
        return None
    return _reindented(text, original)


def _reindented(text: str, original: str) -> str:
    """A one-line suggestion that lost the original line's indentation gets it back, so it applies as is."""
    indent = original[:len(original) - len(original.lstrip())]
    if "\n" in text or not indent or text[:1].isspace():
        return text
    return indent + text
