"""Pure: which number in a brief's sentence is the baseline and which the target, from cue words, so a
metric's roles are decided in code and not by the model.

    "from X to Y"                                         -> baseline X, target Y
    a number after currently / today / baseline / now      -> baseline
    a number after goal / target / success / reach / at least / up to / drop / rise ... -> target
    no cue (e.g. "Support handled 41 tickets")             -> no reading; the model's assignment stands
Window phrases ("within 30 days", "by Q3") are removed first, so their numbers are never a role."""

from __future__ import annotations

import re

from window import strip_windows

_NUM = r"[$€£¥₹]?\d(?:[\d,]*\d)?(?:\.\d+)?(?:\s?(?:k|m|bn|percent|pct)\b|%)?"
_NUMBER = re.compile(_NUM, re.IGNORECASE)
_FROM_TO = re.compile(rf"\bfrom\s+({_NUM}).{{0,40}}?\bto\s+(?:under|below|over|above|at least|about|around)?\s*"
                      rf"({_NUM})", re.IGNORECASE)
_BASELINE_CUE = re.compile(r"\b(?:currently|current|today|baseline|now|at present|stands at|is at)\b", re.IGNORECASE)
_TARGET_CUE = re.compile(r"\b(?:goal|target|success|reach(?:es)?|at least|up to|drops?|falls?|rises?|grows?|"
                         r"increases?|reduces?|cuts?|hits?|achieves?|under|below|above)\b", re.IGNORECASE)


def roles_in(sentence: str) -> tuple[str | None, str | None]:
    """(baseline, target) as the sentence spells them; None where the sentence gives no cue."""
    text = strip_windows(sentence)
    spanned = _FROM_TO.search(text)
    if spanned:
        return spanned.group(1).strip(), spanned.group(2).strip()
    baseline = target = None
    cursor = 0
    for number in _NUMBER.finditer(text):
        lead = text[cursor:number.start()]  # the words since the previous number (or the sentence start)
        cursor = number.end()
        if _BASELINE_CUE.search(lead) and baseline is None:
            baseline = number.group(0).strip()
        elif _TARGET_CUE.search(lead) and target is None:
            target = number.group(0).strip()
    return baseline, target
