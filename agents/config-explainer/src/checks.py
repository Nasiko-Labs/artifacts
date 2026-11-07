"""Pure: code checks on the model's explanation. Every backticked token, ARN and multi-digit number must
appear in the config or the computed facts (normalised); every statement/location must be named; no
"secure/safe/compliant" judgements. A failing sentence is repaired once, then removed, and counted."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from analysis import Analysis

NO_ISSUE = "No issue found in the checks I run."
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_TICKS = re.compile(r"`([^`]+)`")
_ARN = re.compile(r"\barn:aws[\w\-]*:[^\s`'\",)]+")
_NUMBER = re.compile(r"(?<![\w.])\d{2,}(?:\.\d+)?(?![\w])")
_JUDGEMENT = re.compile(r"\b(?:secure|safe|compliant)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*(])")


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").translate(_SMART).casefold().split())


def _flatten(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _flatten(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _flatten(item)
    elif value is not None:
        yield str(value)


def evidence(analysis: Analysis) -> str:
    pieces = [analysis.config, *(_flatten(analysis.facts)), *(g.text for g in analysis.gotchas),
              *(g.evidence for g in analysis.gotchas), *analysis.checked]
    return normalise("\n".join(pieces))


def ungrounded(sentence: str, known: str) -> list[str]:
    spans = [s.strip() for s in _TICKS.findall(sentence)] + _ARN.findall(sentence)
    spans += _NUMBER.findall(_TICKS.sub(" ", sentence))
    return [s for s in spans if s and normalise(s) not in known]


def sentences(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        out += [s for s in _SENTENCE.split(line) if s.strip()] or [""]
    return out


def problems(explanation: str, analysis: Analysis) -> tuple[list[str], list[str]]:
    """(ungrounded spans, items not mentioned)."""
    known = evidence(analysis)
    bad = [span for s in sentences(explanation) for span in ungrounded(s, known)]
    text = normalise(explanation)
    missing = [item for item in analysis.must_mention if normalise(item) not in text]
    return list(dict.fromkeys(bad)), missing


def drop_ungrounded(explanation: str, analysis: Analysis) -> tuple[str, int]:
    """Removes sentences with an ungrounded span. Returns (text, removed count)."""
    known = evidence(analysis)
    kept_lines, removed = [], 0
    for line in (explanation or "").splitlines():
        parts = _SENTENCE.split(line) if line.strip() else [line]
        good = [p for p in parts if not ungrounded(p, known)]
        removed += len(parts) - len(good)
        if good or not line.strip():
            kept_lines.append(" ".join(good))
    return "\n".join(kept_lines).strip(), removed


def no_judgements(text: str) -> str:
    """A sentence calling the config secure/safe/compliant becomes the neutral line."""
    lines = []
    for line in (text or "").splitlines():
        parts = _SENTENCE.split(line) if line.strip() else [line]
        lines.append(" ".join(NO_ISSUE if _JUDGEMENT.search(p) else p for p in parts))
    return "\n".join(lines)
