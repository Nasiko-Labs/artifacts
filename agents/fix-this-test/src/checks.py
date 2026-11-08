"""Pure: the code checks over the model's explanation, and the envelope's computed fields.

- Explanation: a sentence naming a path, identifier, line or number not in the input (or the computed
  counts) is dropped and counted; a "fixed"/"passes" claim that disagrees with `verified` is dropped and the
  verified statement is written by code instead.
- confidence: high = verified, no warnings, <= 20 changed lines, test untouched; medium = verified with a
  warning, or static mode with a patch that compiles; low = everything else.
- needs_human: true unless verified and confidence high; always true when the test changed or
  special-casing is suspected."""

from __future__ import annotations

import re

from inputs import appears_in, normalise

HIGH_MAX_CHANGED_LINES = 20

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_PATH = re.compile(r"[\w./-]+\.py\b")
_TICKED = re.compile(r"`([^`]{1,80})`")
_LINE = re.compile(r"\bline (\d+)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
_CLAIM = re.compile(r"\b(?:now pass(?:es)?|all (?:\d+ )?tests? pass|tests? (?:now )?pass(?:es)?|is fixed|fixe[sd] "
                    r"(?:it|the (?:test|failure|bug))|works now|passing)\b", re.IGNORECASE)


def check_explanation(text: str, files: dict[str, str], failure: str, counts: set[int],
                      verified: bool) -> tuple[str, int]:
    """(kept text, number of sentences dropped)."""
    evidence = failure + "\n" + "\n".join(f"{p}\n{t}" for p, t in files.items())
    longest = max((len(t.splitlines()) for t in files.values()), default=0)
    kept, dropped = [], 0
    for sentence in (s for s in _SENTENCE.split((text or "").strip()) if s.strip()):
        if _grounded(sentence, evidence, longest, counts) and (verified or not _CLAIM.search(sentence)):
            kept.append(sentence)
        else:
            dropped += 1
    return " ".join(kept), dropped


def _grounded(sentence: str, evidence: str, longest: int, counts: set[int]) -> bool:
    plain = normalise(evidence)
    for path in _PATH.findall(sentence):
        if path.rsplit("/", 1)[-1] not in plain:
            return False
    for ticked in _TICKED.findall(sentence):
        if not appears_in(ticked, evidence) and not appears_in(ticked.split("(")[0], evidence):
            return False
    lines = [int(n) for n in _LINE.findall(sentence)]
    if any(n < 1 or n > longest for n in lines):
        return False
    stripped = _LINE.sub("", _TICKED.sub("", sentence))
    for number in _NUMBER.findall(stripped):
        if number not in plain and not (number.isdigit() and int(number) in counts):
            return False
    return True


def confidence(*, verified: bool, mode: str, warnings: list[str], changed: int, changed_test: bool,
               special_casing: bool, finished: bool, compiles: bool, has_patch: bool) -> str:
    if changed_test or special_casing or not has_patch:
        return "low"
    if verified and finished and not warnings and changed <= HIGH_MAX_CHANGED_LINES:
        return "high"
    if verified and finished:
        return "medium"
    if mode == "static" and compiles:
        return "medium"
    return "low"


def needs_human(*, verified: bool, level: str, changed_test: bool, special_casing: bool) -> bool:
    if changed_test or special_casing:
        return True
    return not (verified and level == "high")


def compiles(files: dict[str, str]) -> bool:
    for path, text in files.items():
        if path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except SyntaxError:
                return False
    return True
