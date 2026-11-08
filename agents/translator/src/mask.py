"""Pure: protected spans are replaced by tokens ⟦P1⟧, ⟦P2⟧ ... before the model sees the text, and put back
after. Code, links, emails, mentions, placeholders, glossary "keep" terms and numbers with units or
separators are masked; plain integers in running text stay visible (the digit check covers them)."""

from __future__ import annotations

import re
from collections.abc import Sequence

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_PATTERNS = tuple(re.compile(p, flags) for p, flags in (
    (r"```.*?```", re.DOTALL),                                                   # fenced code
    (r"`[^`\n]+`", 0),                                                           # inline code
    (r"\b(?:https?://|www\.)[^\s<>()\"']*[^\s<>()\"'.,;:!?]", 0),                # URLs
    (r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", 0),                                   # emails
    (r"(?<![\w@#&])[@#][A-Za-z0-9_][\w.-]*\w|(?<![\w@#&])[@#][A-Za-z0-9_]", 0),  # @mentions, #channels
    (r"\{\{[^{}\n]{1,60}\}\}|\{[A-Za-z_]\w{0,40}\}|%\([a-z_]+\)[sd]|%[sd]", 0),  # template placeholders
    (r"\[[^\[\]\n]{1,40}\?\]", 0),                                               # [date?] placeholders
    (r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", 0),                   # numeric dates
    (rf"\b{_MONTH}\.? \d{{1,2}}(?:st|nd|rd|th)?\b(?:,? \d{{4}})?|\b\d{{1,2}} {_MONTH}\b\.?(?: \d{{4}})?", 0),
    (r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\b\.?)?", 0),                # times
    (r"[$€£¥₹]\s?\d[\d,.]*\d|[$€£¥₹]\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|GBP|INR|JPY)\b", 0),  # money
    (r"\b\d+(?:[.,]\d+)?\s?%", 0),                                               # percentages
    (r"\b\d+(?:\.\d+)?\s?(?:kg|km|cm|mm|ms|GB|MB|KB|TB|px|°C|°F|mph|lbs?|oz|g|m|s|h|min)\b", 0),  # units
    (r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b", 0),                      # separators, decimals
))
_TOKEN_VARIANTS = re.compile(r"(?:⟦|\[\[|【|〚)\s*[Pp]\s*(\d{1,4})\s*(?:⟧|\]\]|】|〛)")
TOKEN = re.compile(r"⟦P(\d{1,4})⟧")


def token(n: int) -> str:
    return f"⟦P{n}⟧"


def mask(text: str, keep_terms: Sequence[str] = (), first: int = 1) -> tuple[str, dict[str, str]]:
    """(masked text, token → original span). Earlier patterns win where spans overlap; glossary "keep"
    terms rank after placeholders and before numbers. Tokens are numbered from `first`."""
    patterns = list(_PATTERNS[:7])
    patterns += [re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE) for term in keep_terms if term]
    patterns += list(_PATTERNS[7:])
    taken: list[tuple[int, int]] = []
    for pattern in patterns:
        for found in pattern.finditer(text):
            span = found.span()
            if span[0] < span[1] and not any(span[0] < end and start < span[1] for start, end in taken):
                taken.append(span)
    spans: dict[str, str] = {}
    pieces: list[str] = []
    cursor = 0
    for n, (start, end) in enumerate(sorted(taken), start=first):
        pieces += [text[cursor:start], token(n)]
        spans[token(n)] = text[start:end]
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), spans


def normalize_tokens(text: str) -> str:
    """Folds near-miss token spellings ([[P1]], 【P1】, ⟦ P1 ⟧) back to ⟦P1⟧."""
    return _TOKEN_VARIANTS.sub(lambda m: token(int(m.group(1))), text or "")


def tokens_in(text: str) -> list[str]:
    return [token(int(n)) for n in TOKEN.findall(text or "")]


def restore(text: str, spans: dict[str, str]) -> str:
    return TOKEN.sub(lambda m: spans.get(m.group(0), ""), text or "")


def without_tokens(text: str) -> str:
    return TOKEN.sub(" ", text or "")
