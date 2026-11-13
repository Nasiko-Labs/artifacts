"""Pure: spans the model must never edit become tokens ⟦Pn⟧ before the call and are restored after it.

Masked, in order: code fences, inline code, URLs, emails, direct quotes of 4 or more words (straight or
curly), {{placeholders}} and [word?] placeholders. A later span may contain an earlier token (a quote holding
a link), so tokens are restored from the highest number down."""

from __future__ import annotations

import re
from dataclasses import dataclass

QUOTE_MIN_WORDS = 4
TOKEN_RE = re.compile(r"⟦P(\d+)⟧")

_SPANS = (
    (re.compile(r"```.*?```", re.DOTALL), None),
    (re.compile(r"`[^`\n]+`"), None),
    (re.compile(r"(?:https?://|www\.)[^\s<>()\"'`]+[^\s<>()\"'`.,;:!?]"), None),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), None),
    (re.compile(r"\"[^\"\n]+\"|“[^”\n]+”"), QUOTE_MIN_WORDS),
    (re.compile(r"\{\{[^{}\n]+\}\}"), None),
    (re.compile(r"\[[^\[\]\n]{1,40}\?\]"), None),
)


@dataclass(frozen=True)
class Masked:
    text: str
    originals: tuple[str, ...]

    def token(self, index: int) -> str:
        return f"⟦P{index + 1}⟧"


def mask(text: str) -> Masked:
    current = TOKEN_RE.sub("", text or "")
    originals: list[str] = []
    for pattern, min_words in _SPANS:
        def swap(match: re.Match, min_words=min_words) -> str:
            if min_words and len(match.group(0).split()) < min_words:
                return match.group(0)
            originals.append(match.group(0))
            return f"⟦P{len(originals)}⟧"
        current = pattern.sub(swap, current)
    return Masked(current, tuple(originals))


def unmask(text: str, originals: tuple[str, ...]) -> str:
    restored = text
    for index in range(len(originals), 0, -1):
        restored = restored.replace(f"⟦P{index}⟧", originals[index - 1])
    return restored


def token_problems(model_text: str, originals: tuple[str, ...]) -> list[str]:
    """Every token exactly once; no token we did not issue."""
    problems = []
    found = [int(n) for n in TOKEN_RE.findall(model_text or "")]
    for index in range(1, len(originals) + 1):
        count = found.count(index)
        if count != 1:
            what = "is missing" if count == 0 else f"appears {count} times"
            problems.append(f"the protected span ⟦P{index}⟧ {what}; keep it exactly once, unchanged")
    if any(n < 1 or n > len(originals) for n in found):
        problems.append("the text contains a ⟦P..⟧ token that was not in the draft; remove it")
    return problems


def strip_tokens(text: str) -> str:
    return TOKEN_RE.sub("", text or "")
