"""Pure checks applied to everything the model returns and everything the agent says.

- `Verbatim`: a quote is kept only when it appears, whitespace and quote marks normalised and case
  ignored, in this turn's input or tool output.
- `redact`: lines about compensation or offers are removed before any model sees the text.
- `scrub`: a sentence in the agent's own voice that carries hire/no-hire wording is replaced by the
  fixed note; quoted interviewer text (inside double quotes) is exempt.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from protected_terms import has_decision_words

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_SPACES = re.compile(r"\s+")
_QUOTE_MARKS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "`": "'"})
_REDACT = re.compile(r"\b(?:offer\w*|comp|compensation|salary|salaries|band|equity|pay range)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
_MASK = re.compile(r"\x00(\d+)\x00")
REDACTED_LINE = "[line removed: compensation details are not read]"
MIN_QUOTE_CHARS = 4


def norm(text: str) -> str:
    """Whitespace collapsed, curly quotes straightened, case folded, edge punctuation trimmed."""
    flat = _SPACES.sub(" ", (text or "").translate(_QUOTE_MARKS)).strip().casefold()
    return flat.strip(" .,;:!?\"'()[]-")


@dataclass(frozen=True)
class Verbatim:
    """The text a quote must come from."""

    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "_flat", norm(self.source))

    def has(self, quote: object) -> bool:
        if not isinstance(quote, str):
            return False
        flat = norm(quote)
        return len(flat) >= MIN_QUOTE_CHARS and flat in self._flat  # type: ignore[attr-defined]

    def has_phrase(self, phrase: object) -> bool:
        """A short value (a rating such as "Yes") as whole words, with no minimum length."""
        if not isinstance(phrase, str) or not norm(phrase):
            return False
        return re.search(rf"(?<!\w){re.escape(norm(phrase))}(?!\w)", self._flat) is not None  # type: ignore[attr-defined]

    def kept_sentences(self, text: str) -> str:
        """Only the sentences of `text` that appear verbatim here (a transcription may paraphrase)."""
        kept = [s for s in _SENTENCE_END.split(text or "") if self.has(s)]
        return " ".join(kept).strip()


def parse_answer(text: str) -> dict | None:
    """The JSON object in a model answer (code fences and surrounding prose tolerated), or None."""
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def items(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = answer.get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def text_of(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    return " ".join(value.split()) if isinstance(value, str) else ""


def redact(text: str) -> tuple[str, int]:
    """(text without compensation/offer lines, how many lines were removed)."""
    lines = (text or "").split("\n")
    kept = [REDACTED_LINE if _REDACT.search(line) else line for line in lines]
    return "\n".join(kept), sum(1 for a, b in zip(lines, kept, strict=True) if a != b)


def scrub(text: str) -> tuple[str, int]:
    """(text with every decision-worded sentence replaced by the fixed note, how many were replaced).
    Quoted segments are masked first, so a quote is never split or scanned."""
    lines = [_scrub_line(line) for line in (text or "").split("\n")]
    return "\n".join(line for line, _ in lines), sum(n for _, n in lines)


def _scrub_line(line: str) -> tuple[str, int]:
    quotes: list[str] = []

    def mask(found: re.Match) -> str:
        quotes.append(found.group(0))
        return f"\x00{len(quotes) - 1}\x00"

    def unmask(found: re.Match) -> str:
        return quotes[int(found.group(1))]

    parts, replaced = [], 0
    for sentence in _SENTENCE_END.split(_QUOTED.sub(mask, line)):
        if has_decision_words(sentence):
            replaced += 1
            parts.append(prompts.NO_DECISION_NOTE)
        else:
            parts.append(_MASK.sub(unmask, sentence))
    return " ".join(parts), replaced
